from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.domains.capability.search import PeopleSearchRow
from app.domains.capacity.models import DemandEventKind, DemandOutcome, DemandStatus
from app.domains.capacity.schemas import CapacityInput, WorkloadPublic


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


class ReplyInput(CapacityInput):
    outcome: DemandOutcome
    start_date: date | None = None
    end_date: date | None = None
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    revision: int = Field(strict=True, ge=1)

    @model_validator(mode="before")
    @classmethod
    def discard_client_basis(cls, value: object) -> object:
        if isinstance(value, dict):
            return {k: v for k, v in value.items() if k != "basis"}
        return value

    @model_validator(mode="after")
    def valid_dates(self) -> ReplyInput:
        if self.outcome != DemandOutcome.NOT_NOW and (
            self.start_date is None or self.end_date is None
        ):
            raise ValueError("能做或能做一部分时必须填写承诺起止日期")
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("请同时填写开始和结束日期")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("结束日期不能早于开始日期")
        return self


class RespondInput(CapacityInput):
    action: Literal["accept", "follow_up", "withdraw"]
    text: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
        ]
        | None
    ) = None
    revision: int = Field(strict=True, ge=1)

    @model_validator(mode="after")
    def follow_up_text(self) -> RespondInput:
        if self.action == "follow_up" and not self.text:
            raise ValueError("追问时必须填写说明")
        return self


class ReplyDemandInput(ReplyInput, DemandIdInput):
    pass


class RespondDemandInput(RespondInput, DemandIdInput):
    pass


class DemandBasisPerson(PeopleSearchRow):
    workload: WorkloadPublic


class DemandBasis(CapacityInput):
    data: list[DemandBasisPerson]
    count: int
    reference: CapacityReference


class DemandEventPublic(CapacityInput):
    kind: DemandEventKind
    text: str
    created_at: datetime
    via: str
    outcome: DemandOutcome | None = None
    start_date: date | None = None
    end_date: date | None = None
    basis: CapacityReference | None = None
    responder_name: str | None = None


class DemandPublic(DemandInput):
    id: UUID
    capability_name: str
    status: DemandStatus
    revision: int
    created_at: datetime
    events: list[DemandEventPublic] = Field(default_factory=list)
    committed_start_date: date | None = None
    committed_end_date: date | None = None
    basis_url: str | None = None


class DemandList(BaseModel):
    data: list[DemandPublic]
    count: int
