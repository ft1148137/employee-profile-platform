from datetime import datetime
from uuid import UUID

from sqlmodel import Field, SQLModel

from app.domains.capability.schemas import CapabilityPublic
from app.domains.change_request.claim import ClaimAfter as ClaimAfter
from app.domains.change_request.models import ChangeOp, RequestOrigin, RequestStatus
from app.domains.profile.schemas import ClaimEvidencePublic


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
    evidence: list[ClaimEvidencePublic] = Field(default_factory=list)


class ChangeRequestPublic(SQLModel):
    id: UUID
    origin: RequestOrigin
    status: RequestStatus
    created_at: datetime
    updated_at: datetime
    generated_by: RequestGeneratedBy
    items: list[ChangeItemPublic] = Field(default_factory=list)


class ChangeRequestsPublic(SQLModel):
    data: list[ChangeRequestPublic]
    count: int
