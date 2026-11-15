from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

from app.domains.capacity.models import CapacityRequestStatus, RequesterPolicy

Percent = Annotated[int, Field(strict=True, ge=0, le=200)]
Note = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
Via = Literal["web", "bot", "feishu", "mcp"]


class CapacityInput(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CapacitySettingsInput(CapacityInput):
    enabled: StrictBool = False
    requester_policy: RequesterPolicy = RequesterPolicy.EVERYONE


class WorkloadAdjustInput(CapacityInput):
    base_percent: Percent
    note: Note | None = None


class WorkloadRequestInput(CapacityInput):
    requested_percent: Percent
    reason: Note


class WorkloadDecisionInput(CapacityInput):
    approve: StrictBool
    note: Note | None = None

    @model_validator(mode="after")
    def returned_note(self) -> WorkloadDecisionInput:
        if not self.approve and not self.note:
            raise ValueError("退回时必须填写说明")
        return self


class WorkloadRequestPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID
    employee_name: str | None = None
    requested_percent: int
    reason: str
    status: CapacityRequestStatus
    created_at: datetime
    decided_by_user_id: UUID | None
    decided_for_user_id: UUID | None
    decision_note: str | None
    decided_at: datetime | None


class WorkloadPublic(BaseModel):
    employee_id: UUID
    employee_name: str
    department_id: UUID | None
    department_name: str | None
    jira_task_count: int | None
    base_percent: int | None
    platform_percent: int
    confirmed_percent: int | None
    band: Literal["unconfirmed", "available", "full", "overloaded"]


class MyWorkload(WorkloadPublic):
    requests: list[WorkloadRequestPublic]


class WorkloadList(BaseModel):
    data: list[WorkloadPublic]
    count: int


class WorkloadRequestList(BaseModel):
    data: list[WorkloadRequestPublic]
    count: int
