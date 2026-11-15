from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.domains.capacity.models import DemandEventKind, DemandStatus
from app.domains.capacity.schemas import CapacityInput


class ReferenceInput(CapacityInput):
    target_department_id: UUID
    capability_key: str = Field(min_length=1, max_length=200)
    minimum_level: str | None = Field(default=None, max_length=200)


class CapacityReference(BaseModel):
    department_name: str
    capability_name: str
    count: int
    available: int = 0
    full: int = 0
    overloaded: int = 0
    unconfirmed: int = 0


class DemandInput(ReferenceInput):
    requesting_department_id: UUID
    headcount: int = Field(strict=True, ge=1, le=50)
    allocation_percent: int = Field(strict=True, ge=10, le=100)
    window_start: date
    window_end: date
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]

    @model_validator(mode="after")
    def valid_window(self) -> DemandInput:
        if self.window_end < self.window_start:
            raise ValueError("结束日期不能早于开始日期")
        if self.requesting_department_id == self.target_department_id:
            raise ValueError("目标部门不能是需求方部门本身")
        return self


class DemandListInput(CapacityInput):
    side: Literal["outgoing", "incoming"] = "outgoing"


class DemandIdInput(CapacityInput):
    demand_id: UUID


class DemandEventPublic(CapacityInput):
    kind: DemandEventKind
    text: str
    created_at: datetime
    via: str


class DemandPublic(DemandInput):
    id: UUID
    capability_name: str
    status: DemandStatus
    revision: int
    created_at: datetime
    events: list[DemandEventPublic] = Field(default_factory=list)


class DemandList(BaseModel):
    data: list[DemandPublic]
    count: int
