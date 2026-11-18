from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    computed_field,
    model_validator,
)

from app.domains.planning.models import OptionKind, PlanVerdict

KIND_LABELS: dict[OptionKind, str] = {
    OptionKind.DIRECT: "直接安排",
    OptionKind.NEWCOMER: "安排新人",
    OptionKind.BORROW: "向其他部门借调",
    OptionKind.DELAY: "推迟交付",
    OptionKind.REDUCE_SCOPE: "缩小范围",
    OptionKind.PHASED: "分阶段交付",
    OptionKind.OPTIMIZED: "优化方案",
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
    locked_employee_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_locks(self) -> OptionInput:
        if not set(self.locked_employee_ids) <= {a.employee_id for a in self.assignees}:
            raise ValueError("锁定人员必须已在方案中")
        return self


class ReviseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change: Literal["replace", "dates", "allocation", "phased", "lock", "unlock"]
    from_employee_id: UUID | None = None
    to_employee_id: UUID | None = None
    employee_id: UUID | None = None
    employee_ids: list[UUID] | None = None
    start_date: date | None = None
    end_date: date | None = None
    allocation_percent: int | None = Field(default=None, strict=True, ge=10, le=100)
    split_date: date | None = None

    @model_validator(mode="after")
    def valid_fields(self) -> ReviseInput:
        fields = {
            "replace": ({"from_employee_id", "to_employee_id"}, set()),
            "dates": ({"start_date", "end_date"}, {"employee_id"}),
            "allocation": ({"allocation_percent"}, {"employee_id"}),
            "phased": ({"split_date"}, set()),
            "lock": ({"employee_ids"}, set()),
            "unlock": ({"employee_ids"}, set()),
        }
        required, optional = fields[self.change]
        present = set(self.model_fields_set) - {"change"}
        if (
            not required <= present
            or any(getattr(self, name) is None for name in required)
            or present - required - optional
        ):
            raise ValueError("快捷修改只能提交这一项所需的字段")
        if self.employee_ids is not None and not self.employee_ids:
            raise ValueError("至少选择一名人员")
        return self


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
