import logging
import math
import mimetypes
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from PIL import Image, UnidentifiedImageError
from geoalchemy2 import Geography
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, joinedload

from app.ai import analyze_image, analyze_video, model_health
from app.config import settings
from app.db import SessionLocal, get_db
from app.models import (
    AIAnalysis,
    AuditLog,
    Complaint,
    ComplaintMedia,
    ComplaintStatus,
    ComplaintUpdate,
    Intervention,
    InterventionStatus,
    JobStatus,
    NGOOrganization,
    Notification,
    Role,
    User,
    utc_now,
)
from app.security import create_access_token, get_current_user, hash_password, require_roles, verify_password
from app.storage import download_file, is_object_storage, upload_file

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)
api = APIRouter(prefix="/api")
auth_router = APIRouter(prefix="/auth", tags=["authentication"])

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".mkv"}
ROLE_ADMIN = require_roles(Role.admin)
ROLE_AUTHORITY = require_roles(Role.authority, Role.admin)
ROLE_NGO = require_roles(Role.ngo)


class RegisterPayload(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    role: Role = Role.citizen
    organization_name: str | None = Field(default=None, max_length=180)
    registration_number: str | None = Field(default=None, max_length=120)


class LoginPayload(BaseModel):
    email: EmailStr
    password: str


class ProfilePayload(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)


class StatusPayload(BaseModel):
    status: ComplaintStatus
    remarks: str = Field(default="", max_length=4000)
    rejection_reason: str | None = Field(default=None, max_length=2000)
    assignee_id: int | None = None


class InterventionPayload(BaseModel):
    proposal: str = Field(min_length=10, max_length=4000)


class InterventionStatusPayload(BaseModel):
    status: InterventionStatus
    note: str = Field(default="", max_length=2000)


class RolePayload(BaseModel):
    role: Role
    is_active: bool


def _user_json(user: User) -> dict:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role.value,
        "created_at": user.created_at.isoformat(),
    }


def _audit(
    db: Session,
    actor_id: int | None,
    action: str,
    complaint: Complaint | None = None,
    previous_status: str | None = None,
    new_status: str | None = None,
    details: str = "",
) -> None:
    db.add(
        AuditLog(
            complaint_id=complaint.id if complaint else None,
            actor_id=actor_id,
            action=action,
            previous_status=previous_status,
            new_status=new_status,
            details=details,
        )
    )


def _check_complaint_access(user: User, complaint: Complaint) -> None:
    if user.role in (Role.admin, Role.authority):
        return
    if complaint.reporter_id == user.id:
        return
    if user.role == Role.ngo and complaint.status in (
        ComplaintStatus.verified,
        ComplaintStatus.assigned,
        ComplaintStatus.repair_in_progress,
    ):
        return
    raise HTTPException(status_code=404, detail="Complaint not found.")


def _serialize_complaint(complaint: Complaint, include_private: bool = False) -> dict:
    analyses = sorted(complaint.analyses, key=lambda analysis: analysis.created_at)
    latest = analyses[-1] if analyses else None
    detections = latest.result if latest and latest.status == JobStatus.completed else None
    return {
        "id": complaint.public_id,
        "reporter_id": complaint.reporter_id if include_private else None,
        "reporter_name": complaint.reporter.full_name if include_private and complaint.reporter else "Private citizen",
        "road_name": complaint.road_name,
        "description": complaint.description,
        "latitude": complaint.latitude,
        "longitude": complaint.longitude,
        "status": complaint.status.value,
        "severity": complaint.severity,
        "priority_score": complaint.priority_score,
        "duplicate_of": complaint.duplicate_of.public_id if complaint.duplicate_of else None,
        "rejection_reason": complaint.rejection_reason,
        "assigned_authority_id": complaint.assigned_authority_id,
        "created_at": complaint.created_at.isoformat(),
        "updated_at": complaint.updated_at.isoformat(),
        "resolved_at": complaint.resolved_at.isoformat() if complaint.resolved_at else None,
        "media": [
            {
                "id": media.id,
                "name": media.original_name,
                "type": media.media_type,
                "url": f"/api/media/{media.id}",
                "size": media.file_size,
            }
            for media in complaint.media
        ],
        "analyses": [
            {
                "id": analysis.id,
                "status": analysis.status.value,
                "result": analysis.result,
                "error_message": analysis.error_message,
                "annotated_url": f"/api/analyses/{analysis.id}/annotated" if analysis.annotated_storage_key else None,
                "created_at": analysis.created_at.isoformat(),
            }
            for analysis in analyses
        ],
        "ai_report": detections,
        "updates": [
            {
                "id": update.id,
                "author_id": update.author_id,
                "note": update.note,
                "evidence_url": f"/api/media/update/{update.id}" if update.evidence_storage_key else None,
                "created_at": update.created_at.isoformat(),
            }
            for update in sorted(complaint.updates, key=lambda item: item.created_at)
        ],
        "timeline": [
            {
                "action": event.action,
                "from": event.previous_status,
                "to": event.new_status,
                "remarks": event.details,
                "timestamp": event.created_at.isoformat(),
            }
            for event in sorted(complaint.events, key=lambda item: item.created_at)
        ],
        "interventions": [
            {
                "id": intervention.id,
                "proposal": intervention.proposal,
                "status": intervention.status.value,
                "created_at": intervention.created_at.isoformat(),
                "evidence_url": f"/api/ngo/interventions/{intervention.id}/evidence" if intervention.evidence_key else None,
            }
            for intervention in complaint.interventions
        ],
    }


async def _store_upload(upload: UploadFile, allowed_extensions: set[str]) -> tuple[str, str, int]:
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in allowed_extensions:
        raise HTTPException(status_code=415, detail="Choose a supported image or video file.")
    root = settings.media_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    storage_key = f"original/{uuid4().hex}{extension}"
    destination = root / storage_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with destination.open("wb") as saved:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {settings.upload_max_mb} MB upload limit.",
                    )
                saved.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    if size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    media_type = "image" if extension in ALLOWED_IMAGE_EXTENSIONS else "video"
    try:
        with destination.open("rb") as saved:
            signature = saved.read(16)
        if media_type == "image":
            with Image.open(destination) as image:
                if image.width * image.height > 25_000_000:
                    raise HTTPException(status_code=413, detail="Image dimensions exceed the 25-megapixel processing limit.")
                image.verify()
        else:
            valid_video = (
                signature.startswith(b"\x1a\x45\xdf\xa3")
                or signature.startswith(b"RIFF") and signature[8:12] == b"AVI "
                or len(signature) >= 8 and signature[4:8] == b"ftyp"
            )
            if not valid_video:
                raise HTTPException(status_code=415, detail="The file content does not match a supported video format.")
    except (UnidentifiedImageError, OSError) as error:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail="The file content does not match a supported image format.") from error
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    mime_type = mimetypes.guess_type(upload.filename or "")[0] or (
        "image/jpeg" if media_type == "image" else "video/mp4"
    )
    if is_object_storage():
        upload_file(destination, storage_key, mime_type)
        destination.unlink(missing_ok=True)
    return storage_key, media_type, size


def _process_analysis(analysis_id: int) -> None:
    with SessionLocal() as db:
        analysis = db.get(AIAnalysis, analysis_id)
        if analysis is None:
            logger.error("Analysis job %s disappeared before processing.", analysis_id)
            return
        media = db.get(ComplaintMedia, analysis.media_id)
        if media is None:
            analysis.status = JobStatus.failed
            analysis.error_message = "The original media record is unavailable."
            analysis.completed_at = utc_now()
            db.commit()
            return
        analysis.status = JobStatus.processing
        analysis.started_at = utc_now()
        db.commit()
        root = settings.media_root.resolve()
        output_key = f"annotated/{uuid4().hex}.webm" if media.media_type == "video" else f"annotated/{uuid4().hex}.jpg"
        temp_dir = None
        try:
            if is_object_storage():
                import tempfile

                temp_dir = tempfile.TemporaryDirectory(prefix="civicfix-ai-")
                work_root = Path(temp_dir.name)
                source = download_file(media.storage_key, work_root / f"source{Path(media.original_name).suffix}")
                output_path = work_root / Path(output_key).name
            else:
                source = (root / media.storage_key).resolve()
                if not source.is_relative_to(root):
                    raise ValueError("Media storage path is invalid.")
                output_path = root / output_key
            if media.media_type == "image":
                result = analyze_image(source, output_path)
            else:
                result = analyze_video(source, output_path)
            if is_object_storage():
                upload_file(output_path, output_key, "video/webm" if media.media_type == "video" else "image/jpeg")
            result["annotated_storage_key"] = output_key
            analysis.result = result
            analysis.annotated_storage_key = output_key
            analysis.status = JobStatus.completed
            analysis.completed_at = utc_now()
            complaint = db.get(Complaint, analysis.complaint_id)
            if complaint:
                complaint.severity = result["severity"]
                complaint.priority_score = result["priority_score"]
                db.add(
                    Notification(
                        user_id=complaint.reporter_id,
                        complaint_id=complaint.id,
                        message=f"AI review is ready for {complaint.public_id}: {result['unique_damage_count']} unique damage candidate(s).",
                    )
                )
            _audit(db, None, "AI_ANALYSIS_COMPLETED", complaint, details=result["explanation"])
            db.commit()
        except Exception as error:
            logger.exception("AI analysis job %s failed", analysis_id)
            analysis.status = JobStatus.failed
            analysis.error_message = str(error)[:2000]
            analysis.completed_at = utc_now()
            _audit(db, None, "AI_ANALYSIS_FAILED", db.get(Complaint, analysis.complaint_id), details=str(error)[:2000])
            db.commit()
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()


@auth_router.post("/register", status_code=201)
@limiter.limit("5/minute")
def register(request: Request, payload: RegisterPayload, db: Session = Depends(get_db)):
    if payload.role not in (Role.citizen, Role.ngo):
        raise HTTPException(status_code=400, detail="Only citizen and NGO accounts can self-register.")
    email = str(payload.email).lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    if payload.role == Role.ngo and not (payload.organization_name and payload.registration_number):
        raise HTTPException(status_code=400, detail="NGO accounts require an organization name and registration number.")
    user = User(email=email, full_name=payload.full_name.strip(), password_hash=hash_password(payload.password), role=payload.role)
    db.add(user)
    db.flush()
    if payload.role == Role.ngo:
        db.add(
            NGOOrganization(
                user_id=user.id,
                name=payload.organization_name.strip(),
                registration_number=payload.registration_number.strip(),
            )
        )
    _audit(db, user.id, "ACCOUNT_REGISTERED", details=f"Role: {user.role.value}")
    db.commit()
    db.refresh(user)
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": _user_json(user)}


@auth_router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, payload: LoginPayload, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": _user_json(user)}


@auth_router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_json(user)


@auth_router.patch("/profile")
def update_profile(payload: ProfilePayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.full_name = payload.full_name.strip()
    db.commit()
    db.refresh(user)
    return _user_json(user)


@api.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as error:
        logger.exception("Database health check failed.")
        raise HTTPException(status_code=503, detail="Database is unavailable.") from error
    return {"status": "ok", "database": engine_database_name(db), "environment": settings.app_env}


def engine_database_name(db: Session) -> str:
    return db.get_bind().dialect.name


@api.get("/health/ai")
def ai_health():
    return model_health()


@api.get("/complaints")
def list_complaints(
    status_filter: ComplaintStatus | None = Query(default=None, alias="status"),
    severity: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query =     select(Complaint).options(joinedload(Complaint.reporter), joinedload(Complaint.duplicate_of)).order_by(Complaint.created_at.desc())
    if user.role == Role.citizen:
        query = query.where(Complaint.reporter_id == user.id)
    elif user.role == Role.ngo:
        query = query.where(Complaint.status.in_([ComplaintStatus.verified, ComplaintStatus.assigned, ComplaintStatus.repair_in_progress]))
    if status_filter:
        query = query.where(Complaint.status == status_filter)
    if severity:
        query = query.where(Complaint.severity == severity.upper())
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    complaints = db.scalars(
        query.options(joinedload(Complaint.media), joinedload(Complaint.analyses), joinedload(Complaint.updates), joinedload(Complaint.events), joinedload(Complaint.interventions))
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique().all()
    return {
        "items": [_serialize_complaint(item, include_private=user.role in (Role.authority, Role.admin) or item.reporter_id == user.id) for item in complaints],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@api.get("/complaints/nearby")
def nearby_complaints(
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    radius_meters: int = Query(default=25, ge=1, le=1000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    radius = min(radius_meters, settings.duplicate_radius_meters)
    if db.get_bind().dialect.name == "postgresql":
        distance = func.ST_Distance(
            func.ST_SetSRID(func.ST_MakePoint(Complaint.longitude, Complaint.latitude), 4326).cast(Geography(srid=4326, spatial_index=False)),
            func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326).cast(Geography(srid=4326, spatial_index=False)),
        )
        matches = db.execute(
            select(Complaint, distance.label("distance_meters"))
            .where(
                Complaint.latitude.is_not(None),
                Complaint.longitude.is_not(None),
                Complaint.status.not_in([ComplaintStatus.rejected, ComplaintStatus.resolved]),
                func.ST_DWithin(
                    func.ST_SetSRID(func.ST_MakePoint(Complaint.longitude, Complaint.latitude), 4326).cast(Geography(srid=4326, spatial_index=False)),
                    func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326).cast(Geography(srid=4326, spatial_index=False)),
                    radius,
                ),
            )
            .order_by(distance)
            .limit(10)
        ).all()
        nearby = [
            {
                "id": item.public_id,
                "road_name": item.road_name,
                "status": item.status.value,
                "severity": item.severity,
                "latitude": item.latitude,
                "longitude": item.longitude,
                "distance_meters": round(float(distance_meters or 0)),
            }
            for item, distance_meters in matches
        ]
    else:
        lat_delta = radius / 111_320
        lon_delta = radius / max(111_320 * math.cos(math.radians(latitude)), 1)
        rows = db.scalars(
            select(Complaint).where(
                Complaint.latitude.between(latitude - lat_delta, latitude + lat_delta),
                Complaint.longitude.between(longitude - lon_delta, longitude + lon_delta),
                Complaint.status.not_in([ComplaintStatus.rejected, ComplaintStatus.resolved]),
            )
        ).all()
        nearby = []
        for item in rows:
            dlat = math.radians(item.latitude - latitude)
            dlon = math.radians(item.longitude - longitude)
            haversine = 2 * 6_371_000 * math.asin(
                min(1, math.sqrt(math.sin(dlat / 2) ** 2 + math.cos(math.radians(latitude)) * math.cos(math.radians(item.latitude)) * math.sin(dlon / 2) ** 2))
            )
            if haversine <= radius:
                nearby.append(
                    {
                        "id": item.public_id,
                        "road_name": item.road_name,
                        "status": item.status.value,
                        "severity": item.severity,
                        "latitude": item.latitude,
                        "longitude": item.longitude,
                        "distance_meters": round(haversine),
                    }
                )
        nearby.sort(key=lambda item: item["distance_meters"])
    return {"items": nearby, "radius_meters": radius, "spatial_backend": "PostGIS" if db.get_bind().dialect.name == "postgresql" else "Haversine fallback"}


@api.post("/complaints", status_code=201)
async def create_complaint(
    background_tasks: BackgroundTasks,
    description: str = Form(min_length=10, max_length=8000),
    road_name: str = Form(default="", max_length=180),
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    duplicate_of: str | None = Form(default=None),
    upload: UploadFile | None = File(default=None),
    user: User = Depends(require_roles(Role.citizen)),
    db: Session = Depends(get_db),
):
    if (latitude is None) != (longitude is None):
        raise HTTPException(status_code=400, detail="Provide both latitude and longitude.")
    if latitude is not None and (not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
        raise HTTPException(status_code=400, detail="Coordinates are outside valid ranges.")
    complaint = Complaint(
        reporter_id=user.id,
        description=description.strip(),
        road_name=road_name.strip(),
        latitude=latitude,
        longitude=longitude,
        status=ComplaintStatus.submitted,
    )
    if duplicate_of:
        original = db.scalar(select(Complaint).where(Complaint.public_id == duplicate_of))
        if original is None:
            raise HTTPException(status_code=404, detail="The linked nearby report was not found.")
        if latitude is None or original.latitude is None:
            raise HTTPException(status_code=400, detail="Linked reports require locations.")
        distance_m = _haversine_m(latitude, longitude, original.latitude, original.longitude)
        if distance_m > settings.duplicate_radius_meters:
            raise HTTPException(status_code=400, detail="The selected report is outside the configured duplicate radius.")
        complaint.duplicate_of_id = original.id

    media_record = None
    if upload and upload.filename:
        extension = Path(upload.filename).suffix.lower()
        allowed = ALLOWED_IMAGE_EXTENSIONS if extension in ALLOWED_IMAGE_EXTENSIONS else ALLOWED_VIDEO_EXTENSIONS
        key, media_type, size = await _store_upload(upload, allowed)
    db.add(complaint)
    db.flush()
    if upload and upload.filename:
        media_record = ComplaintMedia(
            complaint_id=complaint.id,
            uploaded_by_id=user.id,
            storage_key=key,
            original_name=Path(upload.filename).name[:255],
            media_type=media_type,
            mime_type=mimetypes.guess_type(upload.filename)[0] or "application/octet-stream",
            file_size=size,
        )
        db.add(media_record)
    _audit(db, user.id, "COMPLAINT_SUBMITTED", complaint, new_status=complaint.status.value, details=description.strip()[:500])
    if duplicate_of:
        _audit(db, user.id, "POSSIBLE_DUPLICATE_LINKED", complaint, details=f"Evidence linked to nearby report {duplicate_of}; this citizen report is retained.")
    db.commit()
    if media_record is not None:
        db.refresh(media_record)
        analysis = AIAnalysis(complaint_id=complaint.id, media_id=media_record.id, status=JobStatus.queued)
        db.add(analysis)
        db.commit()
        db.refresh(analysis)
        background_tasks.add_task(_process_analysis, analysis.id)
    complaint = db.scalar(
        select(Complaint)
        .where(Complaint.id == complaint.id)
        .options(
            joinedload(Complaint.reporter),
            joinedload(Complaint.media),
            joinedload(Complaint.analyses),
            joinedload(Complaint.updates),
            joinedload(Complaint.events),
            joinedload(Complaint.interventions),
            joinedload(Complaint.duplicate_of),
        )
    )
    return _serialize_complaint(complaint, include_private=True)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    value = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000 * math.asin(min(1, math.sqrt(value)))


@api.get("/complaints/{public_id}")
def get_complaint(public_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    complaint = db.scalar(
        select(Complaint)
        .where(Complaint.public_id == public_id)
        .options(
            joinedload(Complaint.reporter),
            joinedload(Complaint.media),
            joinedload(Complaint.analyses),
            joinedload(Complaint.updates),
            joinedload(Complaint.events),
            joinedload(Complaint.interventions),
        )
    )
    if complaint is None:
        raise HTTPException(status_code=404, detail="Complaint not found.")
    _check_complaint_access(user, complaint)
    return _serialize_complaint(complaint, include_private=user.role in (Role.authority, Role.admin) or complaint.reporter_id == user.id)


TRANSITIONS = {
    ComplaintStatus.submitted: {ComplaintStatus.pending_verification, ComplaintStatus.rejected},
    ComplaintStatus.pending_verification: {ComplaintStatus.verified, ComplaintStatus.rejected},
    ComplaintStatus.verified: {ComplaintStatus.assigned, ComplaintStatus.repair_in_progress, ComplaintStatus.rejected},
    ComplaintStatus.assigned: {ComplaintStatus.repair_in_progress, ComplaintStatus.rejected},
    ComplaintStatus.repair_in_progress: {ComplaintStatus.resolved, ComplaintStatus.reopened},
    ComplaintStatus.resolved: {ComplaintStatus.reopened},
    ComplaintStatus.rejected: {ComplaintStatus.reopened},
    ComplaintStatus.reopened: {ComplaintStatus.pending_verification, ComplaintStatus.verified, ComplaintStatus.rejected},
}


@api.patch("/complaints/{public_id}/status")
def change_complaint_status(
    public_id: str,
    payload: StatusPayload,
    background_tasks: BackgroundTasks,
    user: User = Depends(ROLE_AUTHORITY),
    db: Session = Depends(get_db),
):
    complaint = db.scalar(select(Complaint).where(Complaint.public_id == public_id))
    if complaint is None:
        raise HTTPException(status_code=404, detail="Complaint not found.")
    if payload.status == complaint.status or payload.status not in TRANSITIONS[complaint.status]:
        raise HTTPException(status_code=409, detail=f"Status cannot transition from {complaint.status.value} to {payload.status.value}.")
    if payload.status == ComplaintStatus.rejected and not payload.rejection_reason:
        raise HTTPException(status_code=400, detail="A rejection reason is required.")
    if payload.assignee_id:
        assignee = db.get(User, payload.assignee_id)
        if assignee is None or assignee.role != Role.authority or not assignee.is_active:
            raise HTTPException(status_code=400, detail="Choose an active authority account to assign the repair.")
        complaint.assigned_authority_id = assignee.id
    if payload.status == ComplaintStatus.assigned and complaint.assigned_authority_id is None:
        raise HTTPException(status_code=400, detail="Assign an active authority team member before setting the report to ASSIGNED.")
    previous = complaint.status.value
    complaint.status = payload.status
    complaint.rejection_reason = payload.rejection_reason if payload.status == ComplaintStatus.rejected else None
    complaint.resolved_at = utc_now() if payload.status == ComplaintStatus.resolved else None
    db.add(
        ComplaintUpdate(
            complaint_id=complaint.id,
            author_id=user.id,
            note=payload.remarks.strip() or f"Status changed to {payload.status.value}.",
        )
    )
    _audit(db, user.id, "STATUS_CHANGED", complaint, previous, payload.status.value, payload.remarks.strip()[:2000])
    db.add(
        Notification(
            user_id=complaint.reporter_id,
            complaint_id=complaint.id,
            message=f"{complaint.public_id} status updated to {payload.status.value}.",
        )
    )
    db.commit()
    db.refresh(complaint)
    return {"id": complaint.public_id, "status": complaint.status.value}


@api.post("/complaints/{public_id}/updates", status_code=201)
async def add_complaint_update(
    public_id: str,
    note: str = Form(min_length=2, max_length=4000),
    upload: UploadFile | None = File(default=None),
    user: User = Depends(ROLE_AUTHORITY),
    db: Session = Depends(get_db),
):
    complaint = db.scalar(select(Complaint).where(Complaint.public_id == public_id))
    if complaint is None:
        raise HTTPException(status_code=404, detail="Complaint not found.")
    evidence_key = None
    if upload and upload.filename:
        extension = Path(upload.filename).suffix.lower()
        if extension not in ALLOWED_IMAGE_EXTENSIONS:
            raise HTTPException(status_code=415, detail="Repair evidence must be a supported image.")
        evidence_key, _, _ = await _store_upload(upload, ALLOWED_IMAGE_EXTENSIONS)
    update = ComplaintUpdate(complaint_id=complaint.id, author_id=user.id, note=note.strip(), evidence_storage_key=evidence_key)
    db.add(update)
    _audit(db, user.id, "REPAIR_UPDATE_ADDED", complaint, details=note.strip()[:2000])
    db.add(Notification(user_id=complaint.reporter_id, complaint_id=complaint.id, message=f"New repair update on {complaint.public_id}."))
    db.commit()
    return {"id": update.id, "note": update.note}


@api.get("/media/{media_id}")
def get_media(media_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    media = db.get(ComplaintMedia, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found.")
    complaint = db.get(Complaint, media.complaint_id)
    _check_complaint_access(user, complaint)
    return _media_file(media.storage_key, media.mime_type, media.original_name)


@api.get("/media/update/{update_id}")
def get_update_media(update_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    update = db.get(ComplaintUpdate, update_id)
    complaint = db.get(Complaint, update.complaint_id) if update else None
    if update is None or not update.evidence_storage_key:
        raise HTTPException(status_code=404, detail="Repair evidence not found.")
    _check_complaint_access(user, complaint)
    return _media_file(update.evidence_storage_key, "image/jpeg", "repair-evidence.jpg")


@api.get("/analyses/{analysis_id}/annotated")
def get_annotated_media(analysis_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    analysis = db.get(AIAnalysis, analysis_id)
    complaint = db.get(Complaint, analysis.complaint_id) if analysis else None
    if analysis is None or not analysis.annotated_storage_key:
        raise HTTPException(status_code=404, detail="Annotated result is not ready.")
    _check_complaint_access(user, complaint)
    media_type = "video/webm" if analysis.annotated_storage_key.endswith(".webm") else "image/jpeg"
    return _media_file(analysis.annotated_storage_key, media_type, Path(analysis.annotated_storage_key).name)


def _media_file(storage_key: str, media_type: str, filename: str) -> Response:
    if is_object_storage():
        body = download_file(storage_key)
        return StreamingResponse(
            body,
            media_type=media_type,
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{quote(Path(filename).name)}",
                "Cache-Control": "private, no-store",
            },
        )
    root = settings.media_root.resolve()
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="Stored media is unavailable.")
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store"},
    )


@api.get("/analytics/summary")
def analytics_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(Complaint)
    if user.role == Role.citizen:
        query = query.where(Complaint.reporter_id == user.id)
    elif user.role == Role.ngo:
        query = query.where(Complaint.status.in_([ComplaintStatus.verified, ComplaintStatus.assigned, ComplaintStatus.repair_in_progress]))
    scoped = query.subquery()
    status_rows = db.execute(select(scoped.c.status, func.count()).group_by(scoped.c.status)).all()
    severity_rows = db.execute(select(scoped.c.severity, func.count()).group_by(scoped.c.severity)).all()
    total = db.scalar(select(func.count()).select_from(scoped)) or 0
    month_rows = db.execute(
        select(func.strftime("%Y-%m", scoped.c.created_at).label("month"), func.count())
        .group_by("month")
        .order_by("month")
        .limit(12)
    ).all() if db.get_bind().dialect.name == "sqlite" else db.execute(
        select(func.to_char(scoped.c.created_at, "YYYY-MM").label("month"), func.count())
        .group_by("month")
        .order_by("month")
        .limit(12)
    ).all()
    resolved_seconds = db.scalar(
        select(func.avg(func.extract("epoch", Complaint.resolved_at) - func.extract("epoch", Complaint.created_at))).where(
            Complaint.resolved_at.is_not(None),
            Complaint.id.in_(select(scoped.c.id)),
        )
    ) if db.get_bind().dialect.name == "postgresql" else None
    return {
        "total": total,
        "by_status": [{"name": ComplaintStatus[name].value if name in ComplaintStatus.__members__ else str(name), "value": count} for name, count in status_rows],
        "by_severity": [{"name": str(name), "value": count} for name, count in severity_rows],
        "over_time": [{"month": month, "count": count} for month, count in month_rows],
        "average_resolution_hours": round(float(resolved_seconds) / 3600, 1) if resolved_seconds else None,
    }


@api.get("/authority/team")
def authority_team(user: User = Depends(ROLE_AUTHORITY), db: Session = Depends(get_db)):
    users = db.scalars(select(User).where(User.role == Role.authority, User.is_active.is_(True)).order_by(User.full_name)).all()
    return {"items": [_user_json(person) for person in users]}


@api.get("/authority/interventions")
def authority_interventions(user: User = Depends(ROLE_AUTHORITY), db: Session = Depends(get_db)):
    items = db.scalars(
        select(Intervention).where(Intervention.status == InterventionStatus.proposed).order_by(Intervention.created_at.asc())
    ).all()
    return {
        "items": [
            {
                "id": item.id,
                "complaint_id": item.complaint.public_id,
                "organization_id": item.organization_id,
                "proposal": item.proposal,
                "status": item.status.value,
                "created_at": item.created_at.isoformat(),
            }
            for item in items
        ]
    }


@api.patch("/authority/interventions/{intervention_id}")
def authority_decide_intervention(
    intervention_id: int,
    payload: InterventionStatusPayload,
    user: User = Depends(ROLE_AUTHORITY),
    db: Session = Depends(get_db),
):
    if payload.status not in (InterventionStatus.accepted, InterventionStatus.declined):
        raise HTTPException(status_code=400, detail="Authorities can accept or decline a proposed intervention.")
    intervention = db.get(Intervention, intervention_id)
    if intervention is None or intervention.status != InterventionStatus.proposed:
        raise HTTPException(status_code=404, detail="Proposed intervention not found.")
    previous = intervention.status.value
    intervention.status = payload.status
    _audit(
        db,
        user.id,
        "NGO_INTERVENTION_REVIEWED",
        intervention.complaint,
        previous,
        payload.status.value,
        payload.note.strip(),
    )
    db.add(
        Notification(
            user_id=intervention.organization.user_id,
            complaint_id=intervention.complaint_id,
            message=f"Your community proposal for {intervention.complaint.public_id} was {payload.status.value.lower()}.",
        )
    )
    db.commit()
    return {"id": intervention.id, "status": intervention.status.value, "official_complaint_status_unchanged": True}


@api.get("/ngo/organization")
def get_ngo_organization(user: User = Depends(ROLE_NGO), db: Session = Depends(get_db)):
    organization = db.scalar(select(NGOOrganization).where(NGOOrganization.user_id == user.id))
    if organization is None:
        return None
    return _organization_json(organization)


@api.get("/ngo/opportunities")
def ngo_opportunities(user: User = Depends(ROLE_NGO), db: Session = Depends(get_db)):
    organization = db.scalar(select(NGOOrganization).where(NGOOrganization.user_id == user.id))
    if organization is None or organization.verification_status != "VERIFIED":
        return {"items": [], "message": "Your organization must be verified before browsing public cases."}
    complaints = db.scalars(
        select(Complaint)
        .where(Complaint.status.in_([ComplaintStatus.verified, ComplaintStatus.assigned, ComplaintStatus.repair_in_progress]))
        .order_by(Complaint.priority_score.desc(), Complaint.created_at.asc())
        .limit(100)
    ).all()
    return {
        "items": [
            {"id": item.public_id, "road_name": item.road_name, "status": item.status.value, "severity": item.severity, "latitude": item.latitude, "longitude": item.longitude, "priority_score": item.priority_score}
            for item in complaints
        ]
    }


@api.post("/ngo/interventions", status_code=201)
def create_intervention(
    payload: InterventionPayload,
    complaint_id: str,
    user: User = Depends(ROLE_NGO),
    db: Session = Depends(get_db),
):
    organization = db.scalar(select(NGOOrganization).where(NGOOrganization.user_id == user.id))
    if organization is None or organization.verification_status != "VERIFIED":
        raise HTTPException(status_code=403, detail="A verified NGO organization is required.")
    complaint = db.scalar(select(Complaint).where(Complaint.public_id == complaint_id))
    eligible = {ComplaintStatus.verified, ComplaintStatus.assigned, ComplaintStatus.repair_in_progress}
    if complaint is None or complaint.status not in eligible:
        raise HTTPException(status_code=404, detail="This complaint is not open for community support.")
    intervention = Intervention(complaint_id=complaint.id, organization_id=organization.id, proposal=payload.proposal.strip())
    db.add(intervention)
    _audit(db, user.id, "NGO_INTERVENTION_PROPOSED", complaint, details=f"Organization {organization.name} submitted an independent community proposal.")
    db.commit()
    db.refresh(intervention)
    return {"id": intervention.id, "status": intervention.status.value, "official_complaint_status_unchanged": True}


@api.get("/ngo/interventions")
def list_interventions(user: User = Depends(ROLE_NGO), db: Session = Depends(get_db)):
    organization = db.scalar(select(NGOOrganization).where(NGOOrganization.user_id == user.id))
    if organization is None:
        return {"items": []}
    items = db.scalars(select(Intervention).where(Intervention.organization_id == organization.id).order_by(Intervention.created_at.desc())).all()
    return {"items": [{"id": item.id, "complaint_id": item.complaint.public_id, "proposal": item.proposal, "status": item.status.value, "created_at": item.created_at.isoformat(), "evidence_url": f"/api/ngo/interventions/{item.id}/evidence" if item.evidence_key else None} for item in items]}


@api.post("/ngo/interventions/{intervention_id}/evidence", status_code=201)
async def add_intervention_evidence(
    intervention_id: int,
    upload: UploadFile = File(...),
    user: User = Depends(ROLE_NGO),
    db: Session = Depends(get_db),
):
    organization = db.scalar(select(NGOOrganization).where(NGOOrganization.user_id == user.id))
    intervention = db.get(Intervention, intervention_id)
    if organization is None or intervention is None or intervention.organization_id != organization.id:
        raise HTTPException(status_code=404, detail="Community intervention not found.")
    if intervention.status not in (InterventionStatus.in_progress, InterventionStatus.completed):
        raise HTTPException(status_code=409, detail="Evidence can be attached after an accepted intervention has started.")
    key, _, _ = await _store_upload(upload, ALLOWED_IMAGE_EXTENSIONS)
    intervention.evidence_key = key
    intervention.evidence_mime_type = mimetypes.guess_type(upload.filename or "")[0] or "image/jpeg"
    _audit(db, user.id, "NGO_INTERVENTION_EVIDENCE_ADDED", intervention.complaint, details="Community intervention evidence was attached.")
    db.commit()
    return {"id": intervention.id, "evidence_url": f"/api/ngo/interventions/{intervention.id}/evidence"}


@api.get("/ngo/interventions/{intervention_id}/evidence")
def get_intervention_evidence(
    intervention_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    intervention = db.get(Intervention, intervention_id)
    if intervention is None or not intervention.evidence_key:
        raise HTTPException(status_code=404, detail="Community evidence not found.")
    if user.role not in (Role.admin, Role.authority):
        organization = db.scalar(select(NGOOrganization).where(NGOOrganization.user_id == user.id))
        if organization is None or organization.id != intervention.organization_id:
            raise HTTPException(status_code=404, detail="Community evidence not found.")
    return _media_file(
        intervention.evidence_key,
        intervention.evidence_mime_type or "image/jpeg",
        Path(intervention.evidence_key).name,
    )


@api.patch("/ngo/interventions/{intervention_id}")
def update_intervention(
    intervention_id: int,
    payload: InterventionStatusPayload,
    user: User = Depends(ROLE_NGO),
    db: Session = Depends(get_db),
):
    organization = db.scalar(select(NGOOrganization).where(NGOOrganization.user_id == user.id))
    intervention = db.get(Intervention, intervention_id)
    if organization is None or intervention is None or intervention.organization_id != organization.id:
        raise HTTPException(status_code=404, detail="Community intervention not found.")
    allowed = {
        InterventionStatus.proposed: {InterventionStatus.in_progress, InterventionStatus.declined},
        InterventionStatus.accepted: {InterventionStatus.in_progress, InterventionStatus.declined},
        InterventionStatus.in_progress: {InterventionStatus.completed, InterventionStatus.declined},
        InterventionStatus.completed: set(),
        InterventionStatus.declined: set(),
    }
    if payload.status not in allowed[intervention.status]:
        raise HTTPException(status_code=409, detail="Invalid community intervention status transition.")
    previous = intervention.status.value
    intervention.status = payload.status
    _audit(db, user.id, "NGO_INTERVENTION_UPDATED", intervention.complaint, previous, payload.status.value, payload.note.strip())
    db.commit()
    return {"id": intervention.id, "status": intervention.status.value, "official_complaint_status_unchanged": True}


@api.get("/admin/users")
def admin_users(user: User = Depends(ROLE_ADMIN), db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.created_at.desc()).limit(250)).all()
    return {"items": [{**_user_json(person), "is_active": person.is_active} for person in users]}


@api.patch("/admin/users/{user_id}")
def admin_update_user(
    user_id: int,
    payload: RolePayload,
    user: User = Depends(ROLE_ADMIN),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.id == user.id and (payload.role != Role.admin or not payload.is_active):
        raise HTTPException(status_code=400, detail="You cannot remove your own administrator access.")
    before = target.role.value
    target.role = payload.role
    target.is_active = payload.is_active
    _audit(db, user.id, "USER_ACCESS_UPDATED", details=f"user={target.id} role={before}->{target.role.value}, active={target.is_active}")
    db.commit()
    return {**_user_json(target), "is_active": target.is_active}


@api.get("/admin/ngos")
def admin_ngos(user: User = Depends(ROLE_ADMIN), db: Session = Depends(get_db)):
    organizations = db.scalars(select(NGOOrganization).order_by(NGOOrganization.created_at.desc())).all()
    return {"items": [_organization_json(item) for item in organizations]}


@api.patch("/admin/ngos/{organization_id}")
def verify_ngo(
    organization_id: int,
    verification_status: str,
    notes: str = "",
    user: User = Depends(ROLE_ADMIN),
    db: Session = Depends(get_db),
):
    if verification_status not in {"VERIFIED", "REJECTED", "PENDING"}:
        raise HTTPException(status_code=400, detail="Choose VERIFIED, REJECTED, or PENDING.")
    organization = db.get(NGOOrganization, organization_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    organization.verification_status = verification_status
    organization.verification_notes = notes[:2000]
    _audit(db, user.id, "NGO_VERIFICATION_UPDATED", details=f"organization={organization.id}, status={verification_status}")
    db.commit()
    return _organization_json(organization)


def _organization_json(organization: NGOOrganization) -> dict:
    return {
        "id": organization.id,
        "name": organization.name,
        "registration_number": organization.registration_number,
        "verification_status": organization.verification_status,
        "verification_notes": organization.verification_notes,
        "user_id": organization.user_id,
    }


@api.get("/admin/audit")
def audit_log(
    page: int = Query(default=1, ge=1),
    user: User = Depends(ROLE_ADMIN),
    db: Session = Depends(get_db),
):
    items = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).offset((page - 1) * 50).limit(50)).all()
    return {"items": [{"id": item.id, "complaint_id": item.complaint.public_id if item.complaint else None, "actor_id": item.actor_id, "action": item.action, "previous_status": item.previous_status, "new_status": item.new_status, "details": item.details, "created_at": item.created_at.isoformat()} for item in items]}


@api.get("/notifications")
def notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(100)).all()
    return {"items": [{"id": item.id, "complaint_id": item.complaint.public_id if item.complaint else None, "message": item.message, "read": item.read_at is not None, "created_at": item.created_at.isoformat()} for item in items]}


@api.patch("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.get(Notification, notification_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="Notification not found.")
    item.read_at = utc_now()
    db.commit()
    return {"id": item.id, "read": True}


api.include_router(auth_router)
