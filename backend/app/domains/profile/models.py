import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import JsonValue
from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.domains.base import TenantRecord, enum_type, tenant_fk, utc_now


class AssessmentStatus(StrEnum):
    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"
    UNKNOWN = "unknown"
    DISPUTED = "disputed"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class FillStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"


class InferenceStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    GENERATED = "generated"
    FAILED = "failed"


class ProgressReviewStatus(StrEnum):
    NONE = "none"
    PENDING_EMPLOYEE = "pending_employee"
    PENDING_MANAGER = "pending_manager"
    PUBLISHED = "published"
    DISPUTED = "disputed"


class EvidenceQuality(StrEnum):
    SUFFICIENT = "sufficient"
    WEAK = "weak"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class ProfileClaim(TenantRecord, table=True):
    __tablename__ = "profile_claim"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("subject_employee_id", "employee"),
        tenant_fk("capability_definition_id", "capability_definition"),
        tenant_fk("supersedes_claim_id", "profile_claim"),
    )
    subject_employee_id: uuid.UUID
    capability_definition_id: uuid.UUID
    statement: str
    level: str | None = None
    assessment_status: AssessmentStatus = Field(sa_type=enum_type(AssessmentStatus))
    confidence: Confidence | None = Field(default=None, sa_type=enum_type(Confidence))
    explanation: str | None = None
    generated_by: dict[str, JsonValue] = Field(sa_column=Column(JSONB, nullable=False))
    supersedes_claim_id: uuid.UUID | None = None


class ClaimEvidence(SQLModel, table=True):
    __tablename__ = "claim_evidence"
    __table_args__ = (
        tenant_fk("claim_id", "profile_claim"),
        tenant_fk("evidence_id", "evidence"),
    )
    org_id: uuid.UUID = Field(foreign_key="organization.id", index=True)
    claim_id: uuid.UUID = Field(primary_key=True)
    evidence_id: uuid.UUID = Field(primary_key=True)
    relation: EvidenceRelation = Field(sa_type=enum_type(EvidenceRelation))
    note: str | None = None


class ProfileVersion(TenantRecord, table=True):
    """Frozen claim fields and evidence references; never copy evidence content."""

    __tablename__ = "profile_version"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "subject_employee_id", "revision"),
        tenant_fk("subject_employee_id", "employee"),
        tenant_fk("schema_version_id", "capability_schema_version"),
        tenant_fk("supersedes_version_id", "profile_version"),
    )
    subject_employee_id: uuid.UUID
    revision: int
    schema_version_id: uuid.UUID
    snapshot: dict[str, JsonValue] = Field(sa_column=Column(JSONB, nullable=False))
    published_at: datetime = Field(sa_type=DateTime(timezone=True))
    published_by: dict[str, JsonValue] = Field(sa_column=Column(JSONB, nullable=False))
    supersedes_version_id: uuid.UUID | None = None


class ProfileProgress(SQLModel, table=True):
    __tablename__ = "profile_progress"
    __table_args__ = (tenant_fk("employee_id", "employee"),)
    org_id: uuid.UUID = Field(
        primary_key=True, foreign_key="organization.id", index=True
    )
    employee_id: uuid.UUID = Field(primary_key=True)
    fill_status: FillStatus = Field(
        default=FillStatus.NOT_STARTED, sa_type=enum_type(FillStatus)
    )
    inference_status: InferenceStatus = Field(
        default=InferenceStatus.PENDING, sa_type=enum_type(InferenceStatus)
    )
    evidence_quality: EvidenceQuality = Field(
        default=EvidenceQuality.UNKNOWN, sa_type=enum_type(EvidenceQuality)
    )
    inference_error: str | None = None
    incremental_drafted_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
