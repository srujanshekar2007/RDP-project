import logging
import threading
from pathlib import Path

import cv2
import numpy as np
import torch
from huggingface_hub import hf_hub_download, try_to_load_from_cache
from PIL import Image, ImageStat
from torch.nn import (
    BatchNorm2d,
    Conv2d,
    ConvTranspose2d,
    MaxPool2d,
    ModuleList,
    Sequential,
    SiLU,
    Upsample,
)
from ultralytics.nn.modules import Bottleneck, C2f, Concat, Conv, Detect, DFL, Proto, Segment, SPPF
from ultralytics.nn.tasks import DetectionModel, SegmentationModel
from ultralytics.utils import IterableSimpleNamespace
from ultralytics import YOLO

from app.config import settings

logger = logging.getLogger(__name__)
MODEL_ID = settings.model_id
_model = None
_model_lock = threading.Lock()
_inference_lock = threading.Lock()
_safe_classes = [
    getattr,
    BatchNorm2d,
    Conv2d,
    ConvTranspose2d,
    MaxPool2d,
    ModuleList,
    Sequential,
    SiLU,
    Upsample,
    (Bottleneck, "ultralytics.nn.modules.Bottleneck"),
    (C2f, "ultralytics.nn.modules.C2f"),
    (Concat, "ultralytics.nn.modules.Concat"),
    (Conv, "ultralytics.nn.modules.Conv"),
    (Detect, "ultralytics.nn.modules.Detect"),
    (DFL, "ultralytics.nn.modules.DFL"),
    (Proto, "ultralytics.nn.modules.Proto"),
    (Segment, "ultralytics.nn.modules.Segment"),
    (SPPF, "ultralytics.nn.modules.SPPF"),
    DetectionModel,
    SegmentationModel,
    (IterableSimpleNamespace, "ultralytics.yolo.utils.IterableSimpleNamespace"),
]


def load_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        torch.serialization.add_safe_globals(_safe_classes)
        configured_path = settings.model_weights_path.expanduser()
        if not configured_path.is_absolute():
            configured_path = Path(__file__).resolve().parents[1] / configured_path
        if configured_path.is_file():
            source = str(configured_path)
        else:
            cached = try_to_load_from_cache(repo_id=MODEL_ID, filename="best.pt")
            if isinstance(cached, str) and Path(cached).is_file():
                source = cached
            elif settings.model_offline:
                raise FileNotFoundError(
                    f"Offline mode is enabled, but model weights were not found at {configured_path} "
                    f"or in the local Hugging Face cache for {MODEL_ID}."
                )
            else:
                source = hf_hub_download(repo_id=MODEL_ID, filename="best.pt")
        logger.info("Loading road-damage checkpoint from %s", source)
        _model = YOLO(source)
        _model.model.eval()
        return _model


def model_health() -> dict:
    configured_path = settings.model_weights_path.expanduser()
    if not configured_path.is_absolute():
        configured_path = Path(__file__).resolve().parents[1] / configured_path
    return {
        "status": "ready" if _model is not None else "not_loaded",
        "model_id": MODEL_ID,
        "weights_available": configured_path.is_file()
        or isinstance(try_to_load_from_cache(repo_id=MODEL_ID, filename="best.pt"), str),
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "supported_classes": sorted({str(name) for name in (_model.names.values() if _model else ["pothole"])}),
    }


def _severity(detections: list[dict], image_area: int) -> tuple[str, int, list[str]]:
    count = len(detections)
    if not count:
        return "REVIEW_REQUIRED", 0, []
    largest_area = max(item["area_pixels"] for item in detections)
    area_ratio = largest_area / max(image_area, 1)
    average_confidence = sum(item["confidence"] for item in detections) / count
    notes = []
    score = 0
    if count >= settings.reference_damage_count:
        score += 2
        notes.append(f"{count} detections exceed the configured reference of {settings.reference_damage_count}.")
    if area_ratio >= 0.08:
        score += 2
        notes.append("At least one detected region covers a large share of the frame.")
    if average_confidence < 0.35:
        notes.append("Low average model confidence; human review is recommended.")
    if score >= 2:
        severity = "HIGH"
    elif count >= 2 or area_ratio >= 0.025:
        severity = "MEDIUM"
    else:
        severity = "LOW"
    if not notes:
        notes.append("No configured abnormal damage-count pattern was detected.")
    priority = min(100, 25 + score * 20 + min(count, 5) * 5 + round(average_confidence * 10))
    return severity, priority, notes


def _image_quality_warnings(image: Image.Image) -> list[str]:
    gray = np.asarray(image.convert("L"))
    brightness = float(gray.mean())
    contrast = float(gray.std())
    scale = min(1.0, 1024 / max(image.size))
    sample = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    sharpness = float(cv2.Laplacian(sample, cv2.CV_64F).var())
    warnings = []
    if contrast < 4:
        warnings.append("Image has very low contrast; check that it contains usable road evidence.")
    if brightness < 15:
        warnings.append("Image appears underexposed; human review is recommended.")
    elif brightness > 240:
        warnings.append("Image appears overexposed; human review is recommended.")
    if sharpness < 12:
        warnings.append("Image appears blurry; human review is recommended.")
    return warnings


def _requires_human_review(
    detections: list[dict],
    average_confidence: float,
    quality_warnings: list[str] | None = None,
) -> bool:
    return not detections or average_confidence < 0.35 or bool(quality_warnings)


def _serialize_detections(result, image_area: int, frame_number: int | None = None) -> list[dict]:
    detections = []
    if result.boxes is None:
        return detections
    masks = result.masks.data.cpu().numpy() if result.masks is not None else None
    for index, box in enumerate(result.boxes):
        x1, y1, x2, y2 = (float(value) for value in box.xyxy[0].tolist())
        class_id = int(box.cls[0].item())
        item = {
            "class_name": str(result.names[class_id]),
            "confidence": round(float(box.conf[0].item()), 4),
            "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            "area_pixels": max(0, round((x2 - x1) * (y2 - y1))),
            "area_ratio": round(max(0, (x2 - x1) * (y2 - y1)) / max(image_area, 1), 5),
        }
        if masks is not None and index < len(masks):
            item["mask_pixels"] = int(np.count_nonzero(masks[index]))
        if frame_number is not None:
            item["frame"] = frame_number
        detections.append(item)
    return detections


def analyze_image(source_path: Path, output_path: Path, confidence: float | None = None, image_size: int | None = None) -> dict:
    with Image.open(source_path) as source:
        image = source.convert("RGB")
        image.load()
    width, height = image.size
    if width * height > 25_000_000:
        raise ValueError("Image dimensions exceed the 25-megapixel processing limit.")
    model = load_model()
    size = image_size or settings.image_inference_size
    threshold = confidence if confidence is not None else settings.model_confidence_threshold
    with _inference_lock:
        result = model.predict(
            source=cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR),
            conf=threshold,
            imgsz=size,
            verbose=False,
        )[0]
    detections = _serialize_detections(result, width * height)
    annotated_bgr = result.plot()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), annotated_bgr):
        raise RuntimeError("The annotated image could not be saved.")
    severity, priority, patterns = _severity(detections, width * height)
    quality_warnings = _image_quality_warnings(image)
    patterns.extend(quality_warnings)
    average_confidence = sum(item["confidence"] for item in detections) / len(detections) if detections else 0
    if not detections:
        patterns.append("No potholes were detected at this confidence threshold; this is not proof that the report is invalid.")
    quality = "REVIEW_REQUIRED" if _requires_human_review(detections, average_confidence, quality_warnings) else "VALID"
    return {
        "status": quality,
        "model_id": MODEL_ID,
        "processing_mode": "image",
        "image_width": width,
        "image_height": height,
        "inference_size": size,
        "detection_count": len(detections),
        "unique_damage_count": len(detections),
        "damage_types": sorted({item["class_name"] for item in detections}),
        "average_confidence": round(average_confidence, 4),
        "confidence_threshold": threshold,
        "severity": severity,
        "priority_score": priority,
        "abnormal_patterns": patterns,
        "detections": detections,
        "annotated_storage_key": str(output_path),
        "explanation": (
            f"The model found {len(detections)} pothole instance(s) in this image. "
            f"Estimated severity is {severity}; this is an AI estimate and requires human verification."
            + (" Image quality or confidence checks flagged this result for review." if quality == "REVIEW_REQUIRED" else "")
        ),
    }


def _intersection_over_union(left: list[float], right: list[float]) -> float:
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = max(0, left[2] - left[0]) * max(0, left[3] - left[1])
    union += max(0, right[2] - right[0]) * max(0, right[3] - right[1]) - intersection
    return intersection / union if union else 0


def analyze_video(
    source_path: Path,
    output_path: Path,
    confidence: float | None = None,
    image_size: int | None = None,
    frame_stride: int | None = None,
) -> dict:
    source_path = Path(source_path)
    output_path = Path(output_path)
    stride = frame_stride or settings.video_frame_stride
    size = image_size or settings.video_inference_size
    threshold = confidence if confidence is not None else settings.model_confidence_threshold
    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        capture.release()
        raise ValueError("Could not decode this video. Try MP4, MOV, WebM, or AVI.")
    fps = capture.get(cv2.CAP_PROP_FPS)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if fps <= 0 or fps > 120 or frame_count <= 0 or frame_count / fps > settings.video_max_seconds:
        capture.release()
        raise ValueError("Video must be 1–120 FPS and within the configured duration limit.")
    if width <= 0 or height <= 0 or width * height > 25_000_000:
        capture.release()
        raise ValueError("Video dimensions exceed the 25-megapixel limit.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if frame_count > settings.video_max_seconds * 120:
        capture.release()
        raise ValueError("Video frame count exceeds the configured decoding limit.")
    try:
        model = load_model()
    except Exception:
        capture.release()
        raise
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"VP80"), fps, (width, height))
    if not writer.isOpened():
        capture.release()
        writer.release()
        raise RuntimeError("The WebM video encoder is not available on this system.")

    active_tracks: list[dict] = []
    track_counter = 0
    tracked_detections = []
    frame_detections = []
    frames_read = 0
    frames_analyzed = 0
    previous_annotated = None
    last_progress = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frames_read += 1
            if frames_read > frame_count + 5 or frames_read / fps > settings.video_max_seconds + 1:
                raise ValueError("Decoded video exceeds the configured duration limit.")
            if (frames_read - 1) % stride:
                writer.write(previous_annotated if previous_annotated is not None else frame)
                continue
            active_tracks = [
                track
                for track in active_tracks
                if frames_read - track["last_frame"] <= stride * 3
            ]
            matched_track_ids = set()
            with _inference_lock:
                result = model.predict(source=frame, conf=threshold, imgsz=size, verbose=False)[0]
            frames_analyzed += 1
            detections = _serialize_detections(result, width * height, frames_read)
            for detection in detections:
                candidates = [
                    (track, _intersection_over_union(detection["box"], track["box"]))
                    for track in active_tracks
                    if track["class_name"] == detection["class_name"]
                    and track["track_id"] not in matched_track_ids
                    and frames_read - track["last_frame"] <= stride * 3
                ]
                best = max(candidates, key=lambda item: item[1], default=(None, 0))
                if best[0] is not None and best[1] >= 0.25:
                    track = best[0]
                    track["box"] = detection["box"]
                    track["last_frame"] = frames_read
                    track["confidence"] = max(track["confidence"], detection["confidence"])
                    detection["track_id"] = track["track_id"]
                    matched_track_ids.add(track["track_id"])
                else:
                    track_counter += 1
                    track = {
                        "track_id": track_counter,
                        "class_name": detection["class_name"],
                        "box": detection["box"],
                        "last_frame": frames_read,
                        "confidence": detection["confidence"],
                    }
                    active_tracks.append(track)
                    detection["track_id"] = track_counter
                    matched_track_ids.add(track_counter)
                tracked_detections.append(detection)
            frame_detections.extend(detections)
            previous_annotated = result.plot()
            writer.write(previous_annotated)
            if frames_analyzed - last_progress >= 30:
                logger.info("Video inference progress: %s/%s sampled frames", frames_analyzed, frame_count)
                last_progress = frames_analyzed
    finally:
        capture.release()
        writer.release()

    if frames_analyzed == 0:
        raise ValueError("No readable video frames were analyzed.")
    validation = cv2.VideoCapture(str(output_path))
    output_valid = validation.isOpened() and validation.read()[0]
    validation.release()
    if not output_valid:
        raise RuntimeError("The annotated video could not be decoded after encoding.")
    unique_by_id = {}
    for detection in tracked_detections:
        current = unique_by_id.get(detection["track_id"])
        if current is None or detection["confidence"] > current["confidence"]:
            unique_by_id[detection["track_id"]] = detection
    unique_detections = list(unique_by_id.values())
    severity, priority, patterns = _severity(unique_detections, width * height)
    average_confidence = sum(item["confidence"] for item in unique_detections) / len(unique_detections) if unique_detections else 0
    if frames_analyzed < frames_read:
        patterns.append(f"Frame sampling analyzed {frames_analyzed} of {frames_read} decoded frames.")
    return {
        "status": "REVIEW_REQUIRED" if _requires_human_review(unique_detections, average_confidence) else "VALID",
        "model_id": MODEL_ID,
        "processing_mode": "video",
        "video_fps": round(fps, 2),
        "image_width": width,
        "image_height": height,
        "frames_total": frames_read,
        "frames_analyzed": frames_analyzed,
        "frame_stride": stride,
        "inference_size": size,
        "detection_count": len(frame_detections),
        "unique_damage_count": len(unique_detections),
        "damage_types": sorted({item["class_name"] for item in unique_detections}),
        "average_confidence": round(average_confidence, 4),
        "confidence_threshold": threshold,
        "severity": severity,
        "priority_score": priority,
        "abnormal_patterns": patterns,
        "detections": unique_detections,
        "annotated_storage_key": str(output_path),
        "explanation": (
            f"The model analyzed {frames_analyzed} of {frames_read} video frames and tracked "
            f"{len(unique_detections)} unique pothole candidate(s). Severity {severity} is an AI estimate "
            "requiring human verification; tracking uses frame-to-frame box overlap."
        ),
    }
