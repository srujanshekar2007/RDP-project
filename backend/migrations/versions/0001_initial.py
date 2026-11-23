from alembic import op

from app.db import Base
from app import models

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    Base.metadata.create_all(bind=op.get_bind())
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_complaints_location_geography "
            "ON complaints USING GIST "
            "((ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography)) "
            "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        )


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
