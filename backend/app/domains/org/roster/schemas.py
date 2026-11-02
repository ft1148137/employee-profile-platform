from typing import Literal

from pydantic import BaseModel, Field, computed_field

HEADERS = ("工号", "姓名", "部门", "职位", "上级工号", "邮箱")


class RosterRow(BaseModel):
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)
