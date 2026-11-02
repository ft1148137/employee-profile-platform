import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import JsonValue
from sqlalchemy import CheckConstraint, Column, DateTime, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.domains.base import Record, TenantRecord, enum_type, tenant_fk, utc_now


class EmploymentStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Role(StrEnum):
    ORG_ADMIN = "org_admin"
    DEPT_MANAGER = "dept_manager"


class Organization(Record, table=True):
    __tablename__ = "organization"
    slug: str = Field(unique=True)
    name: str
    roster_revision: int = Field(default=0, sa_column_kwargs={"server_default": "0"})


class Department(TenantRecord, table=True):
    __tablename__ = "department"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint(
            "org_id",
            "parent_department_id",
            "name",
            name="uq_department_path",
            postgresql_nulls_not_distinct=True,
        ),
        tenant_fk("parent_department_id", "department"),
    )
    name: str
    parent_department_id: uuid.UUID | None = None


class Employee(TenantRecord, table=True):
    __tablename__ = "employee"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "employee_code"),
        tenant_fk("department_id", "department"),
    )
    department_id: uuid.UUID | None = None
    employee_code: str | None = None
    display_name: str
    title: str | None = None
    work_email: str | None = None
    employment_status: EmploymentStatus = Field(
        default=EmploymentStatus.ACTIVE, sa_type=enum_type(EmploymentStatus)
    )
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )


class ExternalIdentity(SQLModel, table=True):
    __tablename__ = "external_identity"
    __table_args__ = (
        UniqueConstraint("org_id", "source", "external_id"),
        tenant_fk("employee_id", "employee"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid7, primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organization.id", index=True)
    employee_id: uuid.UUID
    source: str
    external_id: str
    display_name: str | None = None
    linked_by: str | None = None
    linked_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )


class OrgMembership(TenantRecord, table=True):
    __tablename__ = "org_membership"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "user_id"),
        tenant_fk("employee_id", "employee"),
        Index(
            "uq_membership_employee",
            "org_id",
            "employee_id",
            unique=True,
            postgresql_where=text("employee_id IS NOT NULL"),
        ),
    )
    user_id: uuid.UUID = Field(foreign_key="user.id")
    employee_id: uuid.UUID | None = None


class RoleGrant(TenantRecord, table=True):
    __tablename__ = "role_grant"
    __table_args__ = (
        tenant_fk("membership_id", "org_membership"),
        tenant_fk("department_id", "department"),
        CheckConstraint(
            "(role = 'dept_manager' AND department_id IS NOT NULL) OR "
            "(role = 'org_admin' AND department_id IS NULL)",
            name="ck_role_department",
        ),
        UniqueConstraint(
            "membership_id",
            "role",
            "department_id",
            name="uq_role_grant",
            postgresql_nulls_not_distinct=True,
        ),
    )
    membership_id: uuid.UUID
    role: Role = Field(sa_type=enum_type(Role))
    department_id: uuid.UUID | None = None
    granted_by_user_id: uuid.UUID | None = Field(default=None, foreign_key="user.id")


class RosterImport(TenantRecord, table=True):
    __tablename__ = "roster_import"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        CheckConstraint(
            "status IN ('previewed', 'applied', 'stale')", name="ck_roster_status"
        ),
    )
    status: str = "previewed"
    filename: str
    file_sha256: str
    rows: list[dict[str, JsonValue]] = Field(sa_column=Column(JSONB, nullable=False))
    column_mapping: dict[str, str] = Field(sa_column=Column(JSONB, nullable=False))
    issues: list[dict[str, JsonValue]] = Field(sa_column=Column(JSONB, nullable=False))
    summary: dict[str, int] = Field(sa_column=Column(JSONB, nullable=False))
    base_revision: int
    uploaded_by_user_id: uuid.UUID = Field(foreign_key="user.id")
    applied_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    applied_by_user_id: uuid.UUID | None = Field(default=None, foreign_key="user.id")


class ReportingLine(TenantRecord, table=True):
    __tablename__ = "reporting_line"
    __table_args__ = (
        tenant_fk("employee_id", "employee"),
        tenant_fk("manager_employee_id", "employee"),
        tenant_fk("roster_import_id", "roster_import"),
        CheckConstraint(
            "employee_id <> manager_employee_id", name="ck_reporting_not_self"
        ),
        Index(
            "uq_current_reporting_line",
            "org_id",
            "employee_id",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
    )
    employee_id: uuid.UUID
    manager_employee_id: uuid.UUID
    valid_from: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
    valid_to: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    source: str = "roster_import"
    roster_import_id: uuid.UUID | None = None
