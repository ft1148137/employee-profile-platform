from datetime import datetime
from typing import Literal
from uuid import UUID

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


class ProgressPublic(SQLModel):
    fill_status: FillStatus
    inference_status: InferenceStatus
    review_status: ProgressReviewStatus
    evidence_quality: EvidenceQuality


class PublishedSummary(SQLModel):
    revision: int
    published_at: datetime


class EmployeeSummary(SQLModel):
    id: UUID
    display_name: str
    title: str | None
    employment_status: EmploymentStatus
    department: DepartmentSummary | None
    progress: ProgressPublic | None
    latest_published: PublishedSummary | None


class EmployeesPublic(SQLModel):
    data: list[EmployeeSummary]
    count: int
