from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.domains.org.models import EmploymentStatus, Role
from app.domains.org.schemas import DepartmentSummary

AccountStatus = Literal["none", "pending_first_login", "active", "disabled"]


class IssueAccounts(BaseModel):
    employee_ids: list[UUID] | None = Field(default=None, max_length=5000)
    regenerate_pending: bool = False


class GrantRole(BaseModel):
    user_id: UUID
    role: Role
    department_id: UUID | None = None

    @model_validator(mode="after")
    def check_department(self) -> Self:
        if (self.role == Role.DEPT_MANAGER) != (self.department_id is not None):
            raise ValueError("部门经理必须指定部门，企业管理员不能指定部门")
        return self


class RolePublic(GrantRole):
    id: UUID
    username: str
    display_name: str | None
    department: DepartmentSummary | None


class AccountPublic(BaseModel):
    user_id: UUID
    username: str
    email: str | None
    status: AccountStatus
    last_login_at: datetime | None


class EmployeeAccount(BaseModel):
    id: UUID
    employee_code: str | None
    display_name: str
    employment_status: EmploymentStatus
    department: DepartmentSummary | None
    account: AccountPublic | None
    roles: list[RolePublic]


class AccountsPublic(BaseModel):
    data: list[EmployeeAccount]
    count: int


class InitialPassword(BaseModel):
    username: str
    display_name: str | None
    initial_password: str
