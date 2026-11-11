from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

HEADERS = ("工号", "姓名", "部门", "职位", "上级工号", "邮箱")


class RosterRow(BaseModel):
    # Internal reconstruction preserves departures; uploaded files cannot set this.
    job_family: str | None = Field(default=None, max_length=50)
    work_location: str | None = Field(default=None, max_length=50)
    hire_date: date | None = None
    inactive: bool = False
    row_number: int
    employee_code: str
    display_name: str
    department_path: tuple[str, ...]
    title: str | None
    manager_code: str | None
    email: str | None


class RosterIssue(BaseModel):
    severity: Literal["error", "warning"]
    row_number: int | None
    column: str | None
    message: str


class ParsedRoster(BaseModel):
    rows: list[RosterRow] = Field(default_factory=list)
    issues: list[RosterIssue] = Field(default_factory=list)
    column_mapping: dict[str, str] = Field(default_factory=dict)

    def active(self) -> ParsedRoster:
        return self.model_copy(
            update={"rows": [r for r in self.rows if not r.inactive]}
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)


ChangeKind = Literal[
    "added", "updated", "transferred", "manager_changed", "deactivated", "reactivated"
]
CHANGE_KINDS: tuple[ChangeKind, ...] = (
    "added",
    "updated",
    "transferred",
    "manager_changed",
    "deactivated",
    "reactivated",
)


class FieldChange(BaseModel):
    field: Literal[
        "display_name",
        "title",
        "email",
        "department",
        "manager",
        "job_family",
        "work_location",
        "hire_date",
    ]
    before: str | None
    after: str | None


class EmployeeChange(BaseModel):
    kind: ChangeKind
    employee_id: UUID | None
    employee_code: str
    display_name: str
    row_number: int | None
    fields: list[FieldChange] = Field(default_factory=list)


class DepartmentNode(BaseModel):
    name: str
    path: tuple[str, ...]
    department_id: UUID | None
    is_new: bool
    employee_count: int = 0
    children: list[DepartmentNode] = Field(default_factory=list)


class RosterDiff(BaseModel):
    summary: dict[str, int]
    changes: list[EmployeeChange]
    departments: list[DepartmentNode]
    created_departments: list[tuple[str, ...]]
    unused_departments: list[tuple[str, ...]]
    warnings: list[RosterIssue]
    blockers: list[str]
    active_count: int
    requires_mass_deactivation_confirm: bool


class RosterImportPreview(RosterDiff):
    id: UUID
    base_revision: int
    column_mapping: dict[str, str]
    issues: list[RosterIssue]
    can_apply: bool


class ApplyImport(BaseModel):
    base_revision: int = Field(ge=0)
    confirm_mass_deactivation: bool = False


class RosterImportResult(BaseModel):
    id: UUID
    status: Literal["previewed", "applied", "stale"]
    summary: dict[str, int]
    issues: list[RosterIssue]


class RosterImportHistory(BaseModel):
    id: UUID
    created_at: datetime
    uploaded_by_username: str
    uploaded_by_full_name: str | None
    filename: str
    status: Literal["previewed", "applied", "stale"]
    summary: dict[str, int]
    applied_at: datetime | None


class RosterImportsPublic(BaseModel):
    data: list[RosterImportHistory]
    count: int
