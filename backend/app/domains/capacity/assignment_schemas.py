from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from app.domains.capacity.models import AssignmentStatus
from app.domains.capacity.schemas import CapacityInput, Note


class AssignmentInput(CapacityInput):
    employee_id: UUID
    allocation_percent: int = Field(strict=True, ge=10, le=100)
    start_date: date
    due_date: date
    outside_basis: StrictBool = False


class AssignmentUpdateInput(CapacityInput):
    allocation_percent: int | None = Field(default=None, strict=True, ge=10, le=100)
    start_date: date | None = None
    due_date: date | None = None
    status: Literal["done", "cancelled"] | None = None
    new_committed_end_date: date | None = None
    commitment_note: Note | None = None

    @model_validator(mode="after")
    def one_change(self) -> AssignmentUpdateInput:
        if (
            sum(
                v is not None
                for v in (
                    self.due_date,
                    self.start_date,
                    self.status,
                    self.allocation_percent,
                )
            )
            != 1
        ):
            raise ValueError("请只修改完成日期、开始日期或关闭分配中的一项")
        if (
            "allocation_percent" in self.model_fields_set
            and self.allocation_percent is None
        ):
            raise ValueError("请填写投入比例")
        if self.due_date is None and self.new_committed_end_date:
            raise ValueError("只有修改完成日期时才能修改承诺日期")
        if self.new_committed_end_date is not None and self.commitment_note is None:
            raise ValueError("推迟承诺时必须填写给对方部门的说明")
        if self.new_committed_end_date is None and self.commitment_note is not None:
            raise ValueError("未修改承诺日期时不能填写承诺说明")
        return self


class AssignmentPublic(CapacityInput):
    proposal_line_id: UUID | None = None
    employee_id: UUID
    allocation_percent: int
    start_date: date | None
    due_date: date
    id: UUID
    demand_id: UUID
    status: AssignmentStatus
    closed_at: datetime | None


class DepartedAssignmentCancelled(CapacityInput):
    id: UUID
    status: Literal["cancelled"]


class DemandAssignment(AssignmentPublic):
    employee_name: str
    employee_departed: bool


class DepartedDemandAssignment(CapacityInput):
    id: UUID
    allocation_percent: int
    start_date: date | None
    due_date: date
    employee_name: Literal["已离职员工"]
    employee_departed: Literal[True]


class AnonymousBorrowAssignment(CapacityInput):
    headcount: int = 1
    allocation_percent: int
    start_date: date | None
    due_date: date
    status: AssignmentStatus


class DemandAssignmentList(CapacityInput):
    data: list[DemandAssignment | DepartedDemandAssignment | AnonymousBorrowAssignment]
    count: int


class MyAssignment(AssignmentPublic):
    reviewer_names: list[str]
    requesting_department_name: str
    capability_name: str
    description: str


class MyAssignmentList(CapacityInput):
    data: list[MyAssignment]
    count: int
