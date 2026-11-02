from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlmodel import SQLModel

from app.domains.evidence.models import EvidenceKind, EvidenceStatus, Visibility
from app.domains.profile.models import EvidenceRelation


class EvidenceSummary(SQLModel):
    id: UUID
    kind: EvidenceKind
    source: str
    source_ref: str
    title: str
    occurred_at: datetime | None
    status: EvidenceStatus
    visibility: Visibility
    redacted: bool


class EvidenceList(SQLModel):
    data: list[EvidenceSummary]
    count: int


class WorkEventPublic(SQLModel):
    action: str
    occurred_at: datetime


class TaskPublic(SQLModel):
    external_key: str
    title: str
    url: str | None


class EvidenceClaimPublic(SQLModel):
    claim_id: UUID
    capability_name: str
    relation: EvidenceRelation


class EvidenceDetail(EvidenceSummary):
    content: dict[str, JsonValue] | None
    provenance: dict[str, JsonValue]
    work_event: WorkEventPublic | None
    task: TaskPublic | None
    supersedes_evidence_id: UUID | None
    claims: list[EvidenceClaimPublic]
