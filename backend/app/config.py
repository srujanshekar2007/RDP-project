from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "sqlite:///./data/civicfix.db"
    jwt_secret: str = "replace-this-with-a-random-secret-at-least-32-characters"
    jwt_expire_minutes: int = Field(default=720, gt=0)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    media_root: Path = Path("./data/media")
    storage_backend: str = "local"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    model_weights_path: Path = Path("./models/best.pt")
    model_offline: bool = False
    model_id: str = "keremberke/yolov8n-pothole-segmentation"
    model_confidence_threshold: float = Field(default=0.25, gt=0, lt=1)
    upload_max_mb: int = Field(default=50, gt=0, le=500)
    video_max_seconds: int = Field(default=120, gt=0, le=600)
    video_frame_stride: int = Field(default=1, gt=0, le=120)
    video_inference_size: int = 640
    image_inference_size: int = Field(default=960, ge=320, le=1536)
    duplicate_radius_meters: int = 25
    reference_damage_count: int = 5

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.upload_max_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
