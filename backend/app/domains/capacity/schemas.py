from datetime import date, datetime
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

from app.domains.capacity.calendar import WeekLoad
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


class WorkloadWeekItem(CapacityInput):
    employee_id: UUID
    other_percent: Percent


class WorkloadWeeksInput(CapacityInput):
    items: Annotated[list[WorkloadWeekItem], Field(min_length=1, max_length=200)]
    start_week: date
    end_week: date

    @model_validator(mode="after")
    def valid_weeks(self) -> WorkloadWeeksInput:
        if self.start_week.weekday() or self.end_week.weekday():
            raise ValueError("请选择周一")
        if not 0 <= (self.end_week - self.start_week).days <= 25 * 7:
            raise ValueError("请选择 1–26 周")
        if len({item.employee_id for item in self.items}) != len(self.items):
            raise ValueError("员工不能重复")
        return self


class WorkloadQuery(CapacityInput):
    from_week: date | None = None
    weeks: int = Field(default=8, ge=1, le=26)

    @model_validator(mode="after")
    def monday(self) -> WorkloadQuery:
        if self.from_week is not None and self.from_week.weekday():
            raise ValueError("请选择周一")
        return self


class WorkloadListQuery(WorkloadQuery):
    department_id: UUID | None = None


class LastConfirmed(BaseModel):
    percent: int
    week_start: date
    confirmed_at: datetime


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
    on_behalf_of_name: str | None = None
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

    weeks: list[WeekLoad]
    last_confirmed: LastConfirmed | None


class MyWorkload(WorkloadPublic):
    requests: list[WorkloadRequestPublic]


class WorkloadList(BaseModel):
    data: list[WorkloadPublic]
    count: int


class WorkloadRequestList(BaseModel):
    data: list[WorkloadRequestPublic]
    count: int
