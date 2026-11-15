from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domains.capacity.models import CapacityRequestStatus
from app.domains.capacity.schemas import CapacityInput, Note, WorkloadDecisionInput


class ExtensionInput(CapacityInput):
    requested_due_date: date
    reason: Note


class RequestExtensionInput(ExtensionInput):
    assignment_id: UUID


class ExtensionDecisionInput(WorkloadDecisionInput):
    new_committed_end_date: date | None = None


class ExtensionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    assignment_id: UUID
    requested_due_date: date
    reason: str
    status: CapacityRequestStatus
    created_at: datetime
    decided_by_user_id: UUID | None
    decided_for_user_id: UUID | None
    decision_note: str | None
    decided_at: datetime | None


class ExtensionList(BaseModel):
    data: list[ExtensionPublic]
    count: int
