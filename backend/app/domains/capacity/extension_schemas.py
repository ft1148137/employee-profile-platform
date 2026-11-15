from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.capacity.models import CapacityRequestStatus
from app.domains.capacity.schemas import CapacityInput, Note, WorkloadDecisionInput


class ExtensionInput(CapacityInput):
    requested_due_date: date
    reason: Note


class RequestExtensionInput(ExtensionInput):
    assignment_id: UUID


class ExtensionDecisionInput(WorkloadDecisionInput):
    new_committed_end_date: date | None = None
    commitment_note: Note | None = None

    @model_validator(mode="after")
    def commitment_fields(self) -> ExtensionDecisionInput:
        if not self.approve and (
            self.new_committed_end_date is not None or self.commitment_note is not None
        ):
            raise ValueError("退回延期时不能修改承诺日期或填写承诺说明")
        if self.new_committed_end_date is not None and self.commitment_note is None:
            raise ValueError("推迟承诺时必须填写给对方部门的说明")
        if self.new_committed_end_date is None and self.commitment_note is not None:
            raise ValueError("未修改承诺日期时不能填写承诺说明")
        return self


class ExtensionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    assignment_id: UUID
    employee_name: str
    commitment_confirmer_names: list[str] = Field(default_factory=list)
    on_behalf_of_name: str | None = None
    reviewer_names: list[str] = Field(default_factory=list)
    demand_id: UUID
    capability_name: str
    current_due_date: date
    committed_end_date: date | None
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
