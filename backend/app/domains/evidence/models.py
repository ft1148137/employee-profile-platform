import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import JsonValue
from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.domains.base import TenantRecord, enum_type, tenant_fk


class EvidenceKind(StrEnum):
    WORK_RECORD = "work_record"
    SELF_REPORT = "self_report"
    MANAGER_REVIEW = "manager_review"


class Visibility(StrEnum):
    STANDARD = "standard"
    RESTRICTED = "restricted"


class EvidenceStatus(StrEnum):
    ACTIVE = "active"
    RETRACTED = "retracted"
    SUPERSEDED = "superseded"


class Evidence(TenantRecord, table=True):
    """Content is immutable. Only status/reason may change, always with an audit event.

    Corrections append a new row referencing the original via supersedes_evidence_id.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint(
            "org_id", "source", "source_ref", "content_hash", name="uq_evidence_content"
        ),
        tenant_fk("subject_employee_id", "employee"),
        tenant_fk("event_id", "work_event"),
        tenant_fk("supersedes_evidence_id", "evidence"),
    )
    subject_employee_id: uuid.UUID
    kind: EvidenceKind = Field(sa_type=enum_type(EvidenceKind))
    source: str
    source_ref: str
    event_id: uuid.UUID | None = None
    title: str
    content: dict[str, JsonValue] = Field(sa_column=Column(JSONB, nullable=False))
    occurred_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    visibility: Visibility = Field(sa_type=enum_type(Visibility))
    provenance: dict[str, JsonValue] = Field(sa_column=Column(JSONB, nullable=False))
    status: EvidenceStatus = Field(
        default=EvidenceStatus.ACTIVE, sa_type=enum_type(EvidenceStatus)
    )
    status_reason: str | None = None
    supersedes_evidence_id: uuid.UUID | None = None
    content_hash: str
