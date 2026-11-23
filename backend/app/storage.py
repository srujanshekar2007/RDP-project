from collections.abc import Iterator
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings


def is_object_storage() -> bool:
    if settings.storage_backend not in {"local", "s3"}:
        raise RuntimeError("STORAGE_BACKEND must be either 'local' or 's3'.")
    if settings.storage_backend == "s3" and not settings.s3_bucket:
        raise RuntimeError("S3_BUCKET is required when STORAGE_BACKEND=s3.")
    return settings.storage_backend == "s3"


def _client():
    if not is_object_storage():
        raise RuntimeError("Object storage was requested while local storage is configured.")
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
    )


def upload_file(path: Path, key: str, content_type: str) -> None:
    try:
        _client().upload_file(
            str(path),
            settings.s3_bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
    except (BotoCoreError, ClientError) as error:
        raise RuntimeError(f"Could not persist media object {key}.") from error


def download_file(key: str, destination: Path | None = None) -> Path | Iterator[bytes]:
    try:
        response = _client().get_object(Bucket=settings.s3_bucket, Key=key)
    except (BotoCoreError, ClientError) as error:
        raise RuntimeError(f"Could not retrieve media object {key}.") from error
    body = response["Body"]
    if destination is None:
        return body.iter_chunks(chunk_size=64 * 1024)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("wb") as saved:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                if chunk:
                    saved.write(chunk)
    finally:
        body.close()
    return destination
