import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import api, limiter
from app.config import settings
from app.db import Base, engine, ensure_postgis_spatial_index
from app import models
from app.storage import is_object_storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.media_root.mkdir(parents=True, exist_ok=True)
    is_object_storage()
    if settings.app_env == "development":
        Base.metadata.create_all(bind=engine)
        ensure_postgis_spatial_index()
    if settings.app_env == "production" and (
        len(settings.jwt_secret) < 32 or settings.jwt_secret.startswith("replace-this")
    ):
        raise RuntimeError("Set a unique JWT_SECRET of at least 32 characters before production startup.")
    logger.info("CivicFix API started using %s storage and %s database.", settings.storage_backend, engine.dialect.name)
    yield


app = FastAPI(
    title="CivicFix API",
    description="Community road-damage reporting and repair tracking.",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(Exception)
async def unhandled_error(_: Request, error: Exception):
    logger.exception("Unhandled API error", exc_info=error)
    return JSONResponse(status_code=500, content={"detail": "An unexpected server error occurred."})


@app.get("/")
def root():
    return {"name": "CivicFix", "api_docs": "/docs", "health": "/api/health"}


app.include_router(api)
