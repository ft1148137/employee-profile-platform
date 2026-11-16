from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field

from app.domains.planning.models import OptionKind, PlanVerdict

KIND_LABELS: dict[OptionKind, str] = {
    OptionKind.DIRECT: "直接安排",
    OptionKind.NEWCOMER: "安排新人",
    OptionKind.BORROW: "向其他部门借调",
    OptionKind.DELAY: "推迟交付",
    OptionKind.REDUCE_SCOPE: "缩小范围",
    OptionKind.PHASED: "分阶段交付",
    OptionKind.CUSTOM: "自定方案",
}
VERDICT_LABELS: dict[PlanVerdict, str] = {
    PlanVerdict.FEASIBLE: "可行",
    PlanVerdict.CONDITIONAL: "有条件可行",
    PlanVerdict.INFEASIBLE: "不可行",
    PlanVerdict.INSUFFICIENT_DATA: "数据不足",
}


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def verdict_label(self) -> str:
        return VERDICT_LABELS[self.verdict]


class CasePerson(BaseModel):
    employee_id: UUID
    display_name: str


class CasePublic(BaseModel):
    id: UUID
    demand_id: UUID
    snapshot_id: UUID
    snapshot_created_at: datetime
    data: dict[str, JsonValue]
    stale: bool
    notes: list[str]
    people: list[CasePerson]
    options: list[OptionPublic]
