from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints
from sqlmodel import SQLModel

from app.domains.org.models import EmploymentStatus, Role
from app.domains.profile.models import (
    EvidenceQuality,
    FillStatus,
    InferenceStatus,
    ProgressReviewStatus,
)


class OrganizationPublic(SQLModel):
    id: UUID
    name: str
    slug: str
    employee_fields: list[Literal["job_family", "work_location", "hire_date"]] = []


class OrganizationUpdate(SQLModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]


class MembershipPublic(SQLModel):
    org: OrganizationPublic
    employee_id: UUID | None
    roles: list[Role | Literal["employee"]]
    managed_department_ids: list[UUID]


class DepartmentSummary(SQLModel):
    id: UUID
    name: str


class DepartmentPublic(DepartmentSummary):
    parent_department_id: UUID | None


class DepartmentsPublic(SQLModel):
    data: list[DepartmentPublic]
    count: int


class EmployeeReference(SQLModel):
    id: UUID
    display_name: str


class DepartmentManager(SQLModel):
    employee_id: UUID | None
    display_name: str


class OrgChartDepartment(DepartmentPublic):
    managers: list[DepartmentManager] = []
    direct_count: int = 0
    total_count: int = 0


class OrgChartPerson(EmployeeReference):
    title: str | None
    department_id: UUID | None
    manager_id: UUID | None


class OrgChartPublic(SQLModel):
    departments: list[OrgChartDepartment]
    people: list[OrgChartPerson]


class InitialProfileStatus(StrEnum):
    WAITING_SOURCE = "waiting_source"
    NO_RECORDS = "no_records"
    WAITING_DRAFT = "waiting_draft"
    DRAFTING = "drafting"
    PENDING_OWNER = "pending_owner"
    NO_JUDGMENT = "no_judgment"
    FAILED = "failed"
    DONE = "done"


class ProgressPublic(SQLModel):
    fill_status: FillStatus
    inference_status: InferenceStatus
    review_status: ProgressReviewStatus
    evidence_quality: EvidenceQuality
    initial_profile_status: InitialProfileStatus


class PublishedSummary(SQLModel):
    revision: int
    published_at: datetime


class EmployeeSummary(SQLModel):
    job_family: str | None = Field(default=None, exclude_if=lambda v: v is None)
    work_location: str | None = Field(default=None, exclude_if=lambda v: v is None)
    hire_date: date | None = Field(default=None, exclude_if=lambda v: v is None)
    id: UUID
    display_name: str
    title: str | None
    employment_status: EmploymentStatus
    department: DepartmentSummary | None
    progress: ProgressPublic
    latest_published: PublishedSummary | None
    manager: EmployeeReference | None = None


class EmployeesPublic(SQLModel):
    data: list[EmployeeSummary]
    count: int
