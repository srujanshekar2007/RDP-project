from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def ensure_postgis_spatial_index() -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_complaints_location_geography "
                "ON complaints USING GIST "
                "((ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography)) "
                "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
            )
        )
