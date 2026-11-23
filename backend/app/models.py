import enum
import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    citizen = "citizen"
    authority = "authority"
    ngo = "ngo"
    admin = "admin"


class ComplaintStatus(str, enum.Enum):
    submitted = "SUBMITTED"
    pending_verification = "PENDING_VERIFICATION"
    verified = "VERIFIED"
    assigned = "ASSIGNED"
    repair_in_progress = "REPAIR_IN_PROGRESS"
    resolved = "RESOLVED"
    rejected = "REJECTED"
    reopened = "REOPENED"


class JobStatus(str, enum.Enum):
    queued = "QUEUED"
    processing = "PROCESSING"
    completed = "COMPLETED"
    failed = "FAILED"


class InterventionStatus(str, enum.Enum):
    proposed = "PROPOSED"
    accepted = "ACCEPTED"
    in_progress = "IN_PROGRESS"
    completed = "COMPLETED"
    declined = "DECLINED"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.citizen, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    complaints: Mapped[list["Complaint"]] = relationship(
        back_populates="reporter",
        foreign_keys="Complaint.reporter_id",
    )


class Complaint(Base):
    __tablename__ = "complaints"
    __table_args__ = (
        CheckConstraint("latitude IS NULL OR (latitude >= -90 AND latitude <= 90)"),
        CheckConstraint("longitude IS NULL OR (longitude >= -180 AND longitude <= 180)"),
        Index("ix_complaints_status_severity_created", "status", "severity", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(24), unique=True, index=True, default=lambda: f"CF-{secrets.token_hex(5).upper()}"
    )
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("complaints.id", ondelete="SET NULL"), index=True)
    road_name: Mapped[str] = mapped_column(String(180), default="")
    description: Mapped[str] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    status: Mapped[ComplaintStatus] = mapped_column(
        Enum(ComplaintStatus), default=ComplaintStatus.submitted, index=True
    )
    severity: Mapped[str] = mapped_column(String(16), default="REVIEW_REQUIRED", index=True)
    priority_score: Mapped[int] = mapped_column(Integer, default=0)
    assigned_authority_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    reporter: Mapped[User] = relationship(back_populates="complaints", foreign_keys=[reporter_id])
    duplicate_of: Mapped["Complaint | None"] = relationship(
        remote_side="Complaint.id",
        foreign_keys=[duplicate_of_id],
    )
    media: Mapped[list["ComplaintMedia"]] = relationship(back_populates="complaint", cascade="all, delete-orphan")
    analyses: Mapped[list["AIAnalysis"]] = relationship(back_populates="complaint", cascade="all, delete-orphan")
    updates: Mapped[list["ComplaintUpdate"]] = relationship(back_populates="complaint", cascade="all, delete-orphan")
    events: Mapped[list["AuditLog"]] = relationship(back_populates="complaint")
    interventions: Mapped[list["Intervention"]] = relationship(back_populates="complaint")


class ComplaintMedia(Base):
    __tablename__ = "complaint_media"
    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), index=True)
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    original_name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(20))
    mime_type: Mapped[str] = mapped_column(String(120))
    file_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    complaint: Mapped[Complaint] = relationship(back_populates="media")


class AIAnalysis(Base):
    __tablename__ = "ai_analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("complaint_media.id", ondelete="CASCADE"))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, index=True)
    result: Mapped[dict | None] = mapped_column(JSON)
    annotated_storage_key: Mapped[str | None] = mapped_column(String(500))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    complaint: Mapped[Complaint] = relationship(back_populates="analyses")


class ComplaintUpdate(Base):
    __tablename__ = "complaint_updates"
    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    note: Mapped[str] = mapped_column(Text)
    evidence_storage_key: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    complaint: Mapped[Complaint] = relationship(back_populates="updates")


class NGOOrganization(Base):
    __tablename__ = "ngo_organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    name: Mapped[str] = mapped_column(String(180))
    registration_number: Mapped[str] = mapped_column(String(120))
    verification_status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    verification_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Intervention(Base):
    __tablename__ = "interventions"
    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("ngo_organizations.id", ondelete="CASCADE"), index=True)
    proposal: Mapped[str] = mapped_column(Text)
    status: Mapped[InterventionStatus] = mapped_column(
        Enum(InterventionStatus), default=InterventionStatus.proposed, index=True
    )
    evidence_key: Mapped[str | None] = mapped_column(String(500))
    evidence_mime_type: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    complaint: Mapped[Complaint] = relationship(back_populates="interventions")
    organization: Mapped[NGOOrganization] = relationship()


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    complaint_id: Mapped[int | None] = mapped_column(ForeignKey("complaints.id", ondelete="SET NULL"))
    message: Mapped[str] = mapped_column(String(500))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    complaint: Mapped[Complaint | None] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int | None] = mapped_column(ForeignKey("complaints.id", ondelete="SET NULL"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    previous_status: Mapped[str | None] = mapped_column(String(32))
    new_status: Mapped[str | None] = mapped_column(String(32))
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    complaint: Mapped[Complaint | None] = relationship(back_populates="events")
