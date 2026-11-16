from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.domains.planning.models import OptionKind, PlanVerdict


class AssigneeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: UUID
    allocation_percent: int = Field(strict=True, ge=10, le=100)
    start_date: date
    end_date: date


class OptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignees: list[AssigneeInput] = Field(min_length=1)


class OptionPublic(BaseModel):
    id: UUID
    snapshot_id: UUID
    kind: OptionKind
    params: dict[str, JsonValue]
    verdict: PlanVerdict
    findings: list[dict[str, JsonValue]]
    summary: dict[str, JsonValue]
    created_at: datetime


class CasePublic(BaseModel):
    id: UUID
    demand_id: UUID
    snapshot_id: UUID
    snapshot_created_at: datetime
    data: dict[str, JsonValue]
    stale: bool
    options: list[OptionPublic]
