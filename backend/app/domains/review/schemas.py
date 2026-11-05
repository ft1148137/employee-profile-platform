from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator
from sqlmodel import SQLModel

from app.domains.change_request.models import ItemDecision, RequestStatus, SubmissionVia
from app.domains.change_request.schemas import ChangeItemPublic, ToolInput
from app.domains.evidence.schemas import EvidenceDetail
from app.domains.profile.schemas import ClaimPublic


class ReviewSummary(SQLModel):
    id: UUID
    subject_employee_id: UUID
    employee_name: str
    department_name: str | None
    submitted_at: datetime | None
    submitted_via: SubmissionVia | None
    client_name: str | None
    owner_reviewed: bool
    item_count: int
    can_decide: bool
    unavailable_reason: str | None = None


class PendingReviews(SQLModel):
    data: list[ReviewSummary]
    count: int


class ReviewItem(SQLModel):
    before: ClaimPublic | None
    after: ChangeItemPublic
    evidence: list[EvidenceDetail]
    jira_only: bool
    approvable: bool
    approval_blocked_reason: str | None


class ReviewDetail(SQLModel):
    id: UUID
    revision: int
    items: list[ReviewItem]


class DecisionInput(ToolInput):
    item_id: UUID
    decision: ItemDecision
    reason: str | None = None

    @model_validator(mode="after")
    def require_reason(self) -> DecisionInput:
        if self.decision != ItemDecision.APPROVED and not (self.reason or "").strip():
            raise ValueError("退回和拒绝必须填写原因")
        return self


class DecideInput(ToolInput):
    revision: int = Field(ge=1)
    decisions: list[DecisionInput] = Field(min_length=1)
    comment: str | None = None


class DecisionResult(SQLModel):
    request_id: UUID
    decision_id: UUID
    status: RequestStatus
    revision: int
    published_version_id: UUID | None
    returned_request_id: UUID | None
