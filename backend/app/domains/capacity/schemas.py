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
    start_week: date
    end_week: date

    @model_validator(mode="after")
    def valid_weeks(self) -> WorkloadRequestInput:
        if self.start_week.weekday() or self.end_week.weekday():
            raise ValueError("请选择周一")
        if not 0 <= (self.end_week - self.start_week).days <= 25 * 7:
            raise ValueError("请选择 1–26 周")
        return self


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
    reviewer_names: list[str] = Field(default_factory=list)
    requested_percent: int
    start_week: date | None
    end_week: date | None
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
    correction_pending: dict[date, bool]
    last_confirmed: LastConfirmed | None


class MyWorkload(WorkloadPublic):
    requests: list[WorkloadRequestPublic]
    reviewer_names: list[str]


class WorkloadList(BaseModel):
    data: list[WorkloadPublic]
    count: int


class WorkloadRequestList(BaseModel):
    data: list[WorkloadRequestPublic]
    count: int


class UnavailabilityInput(CapacityInput):
    employee_id: UUID
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def valid_dates(self) -> UnavailabilityInput:
        if not 0 <= (self.end_date - self.start_date).days < 92:
            raise ValueError("请选择先后有序的 1–92 天")
        return self


class UnavailabilityQuery(CapacityInput):
    employee_id: UUID
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")

    @model_validator(mode="after")
    def valid_dates(self) -> UnavailabilityQuery:
        if self.to_date < self.from_date:
            raise ValueError("结束日期不能早于开始日期")
        return self


class UnavailabilityPublic(UnavailabilityInput):
    id: UUID
    created_by_user_id: UUID
    created_at: datetime
    can_delete: bool
    over_weeks: list[date]


class UnavailabilityList(BaseModel):
    data: list[UnavailabilityPublic]
    count: int
