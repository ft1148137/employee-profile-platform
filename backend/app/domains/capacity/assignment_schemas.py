from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domains.capacity.models import AssignmentStatus
from app.domains.capacity.schemas import CapacityInput


class AssignmentInput(CapacityInput):
    employee_id: UUID
    allocation_percent: int = Field(strict=True, ge=10, le=100)
    due_date: date


class AssignmentUpdateInput(CapacityInput):
    due_date: date | None = None
    status: Literal["done", "cancelled"] | None = None
    new_committed_end_date: date | None = None

    @model_validator(mode="after")
    def one_change(self) -> AssignmentUpdateInput:
        if (self.due_date is None) == (self.status is None):
            raise ValueError("请修改完成日期或关闭分配")
        if self.status and self.new_committed_end_date:
            raise ValueError("关闭分配时不能修改承诺日期")
        return self


class AssignmentPublic(AssignmentInput):
    id: UUID
    demand_id: UUID
    status: AssignmentStatus
    closed_at: datetime | None


class MyAssignment(AssignmentPublic):
    requesting_department_name: str
    capability_name: str
    description: str


class MyAssignmentList(CapacityInput):
    data: list[MyAssignment]
    count: int
