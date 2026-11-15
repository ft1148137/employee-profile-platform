from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domains.capacity.models import AssignmentStatus
from app.domains.capacity.schemas import CapacityInput, Note


class AssignmentInput(CapacityInput):
    employee_id: UUID
    allocation_percent: int = Field(strict=True, ge=10, le=100)
    due_date: date


class AssignmentUpdateInput(CapacityInput):
    due_date: date | None = None
    status: Literal["done", "cancelled"] | None = None
    new_committed_end_date: date | None = None
    commitment_note: Note | None = None

    @model_validator(mode="after")
    def one_change(self) -> AssignmentUpdateInput:
        if (self.due_date is None) == (self.status is None):
            raise ValueError("请修改完成日期或关闭分配")
        if self.status and self.new_committed_end_date:
            raise ValueError("关闭分配时不能修改承诺日期")
        if self.new_committed_end_date is not None and self.commitment_note is None:
            raise ValueError("推迟承诺时必须填写给对方部门的说明")
        if self.new_committed_end_date is None and self.commitment_note is not None:
            raise ValueError("未修改承诺日期时不能填写承诺说明")
        return self


class AssignmentPublic(AssignmentInput):
    id: UUID
    demand_id: UUID
    status: AssignmentStatus
    closed_at: datetime | None


class DemandAssignment(AssignmentPublic):
    employee_name: str


class DemandAssignmentList(CapacityInput):
    data: list[DemandAssignment]
    count: int


class MyAssignment(AssignmentPublic):
    reviewer_names: list[str]
    requesting_department_name: str
    capability_name: str
    description: str


class MyAssignmentList(CapacityInput):
    data: list[MyAssignment]
    count: int
