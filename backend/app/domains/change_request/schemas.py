from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlmodel import Field, SQLModel

from app.domains.capability.schemas import CapabilityPublic
from app.domains.change_request.claim import ClaimAfter as ClaimAfter
from app.domains.change_request.models import (
    ChangeOp,
    RequestOrigin,
    RequestStatus,
    SubmissionVia,
)
from app.domains.profile.schemas import ClaimEvidencePublic, ClaimPublic


class RequestGeneratedBy(SQLModel):
    mode: str | None = None
    model: str | None = None


class ChangeItemPublic(SQLModel):
    id: UUID
    op: ChangeOp
    capability: CapabilityPublic
    target_claim_id: UUID | None
    after: ClaimAfter | None
    explanation: str | None
    uncertainty: str | None
    owner_statement: str | None = None
    objection: str | None = None
    evidence: list[ClaimEvidencePublic] = Field(default_factory=list)


class ChangeRequestPublic(SQLModel):
    id: UUID
    origin: RequestOrigin
    status: RequestStatus
    created_at: datetime
    updated_at: datetime
    generated_by: RequestGeneratedBy
    revision: int
    submitted_via: SubmissionVia | None
    client_name: str | None
    submitted_at: datetime | None
    items: list[ChangeItemPublic] = Field(default_factory=list)


class ChangeRequestsPublic(SQLModel):
    data: list[ChangeRequestPublic]
    count: int


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemInput(ToolInput):
    op: ChangeOp
    capability_key: str
    statement: str = Field(min_length=1)
    level: str | None = None
    owner_statement: str | None = None
    objection: str | None = None
    evidence_ids: list[UUID] = Field(default_factory=list)
    uncertainty: str | None = None


class DraftInput(ToolInput):
    items: list[ItemInput]


class SubmitInput(ToolInput):
    revision: int = Field(ge=1)


class PreviewItem(SQLModel):
    before: ClaimPublic | None
    after: ChangeItemPublic
    warning: str | None = None


class RequestPreview(SQLModel):
    request_id: UUID
    revision: int
    items: list[PreviewItem]
