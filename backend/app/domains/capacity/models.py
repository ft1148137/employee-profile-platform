from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.domains.base import TenantRecord, enum_type, tenant_fk, utc_now


def member_fk(column: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["org_id", column], ["org_membership.org_id", "org_membership.user_id"]
    )


class RequesterPolicy(StrEnum):
    EVERYONE = "everyone"
    MANAGERS = "managers"


class CapacityRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    RETURNED = "returned"


class DemandStatus(StrEnum):
    SUBMITTED = "submitted"
    REPLIED = "replied"
    ACCEPTED = "accepted"
    WITHDRAWN = "withdrawn"


class DemandEventKind(StrEnum):
    SUBMITTED = "submitted"
    REPLIED = "replied"
    FOLLOW_UP = "follow_up"
    ACCEPTED = "accepted"
    WITHDRAWN = "withdrawn"
    COMMITMENT_CHANGED = "commitment_changed"


class DemandOutcome(StrEnum):
    CAN_DO = "can_do"
    PARTIAL = "partial"
    NOT_NOW = "not_now"


class AssignmentStatus(StrEnum):
    ACTIVE = "active"
    DONE = "done"
    CANCELLED = "cancelled"


class CapacitySetting(TenantRecord, table=True):
    __tablename__ = "capacity_setting"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id"),
        member_fk("updated_by_user_id"),
    )
    enabled: bool = False
    requester_policy: RequesterPolicy = Field(
        default=RequesterPolicy.EVERYONE, sa_type=enum_type(RequesterPolicy)
    )
    updated_by_user_id: UUID
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )


class CapacityWeek(TenantRecord, table=True):
    __tablename__ = "capacity_week"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "employee_id", "week_start"),
        tenant_fk("employee_id", "employee"),
        member_fk("confirmed_by_user_id"),
        CheckConstraint("other_percent BETWEEN 0 AND 200", name="week_percent"),
        CheckConstraint("extract(isodow from week_start) = 1", name="week_monday"),
    )
    employee_id: UUID
    week_start: date
    other_percent: int
    confirmed_by_user_id: UUID
    confirmed_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )


class CapacityUnavailability(TenantRecord, table=True):
    __tablename__ = "capacity_unavailability"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("employee_id", "employee"),
        member_fk("created_by_user_id"),
        CheckConstraint("end_date >= start_date", name="unavailability_dates"),
    )
    employee_id: UUID
    start_date: date
    end_date: date
    created_by_user_id: UUID


class CapacityDecision(SQLModel):
    status: CapacityRequestStatus = Field(
        default=CapacityRequestStatus.PENDING, sa_type=enum_type(CapacityRequestStatus)
    )
    reason: str = Field(min_length=1, max_length=500)
    decided_by_user_id: UUID | None = None
    decided_for_user_id: UUID | None = None
    decision_note: str | None = Field(default=None, max_length=500)
    decided_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class CapacityWorkloadRequest(CapacityDecision, TenantRecord, table=True):
    __tablename__ = "capacity_workload_request"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("employee_id", "employee"),
        member_fk("decided_by_user_id"),
        member_fk("decided_for_user_id"),
        CheckConstraint(
            "requested_percent BETWEEN 0 AND 200", name="requested_percent"
        ),
        CheckConstraint("length(reason) BETWEEN 1 AND 500", name="workload_reason"),
        CheckConstraint(
            "(start_week IS NULL AND end_week IS NULL) OR "
            "(start_week IS NOT NULL AND end_week IS NOT NULL AND start_week <= end_week "
            "AND extract(isodow from start_week) = 1 AND extract(isodow from end_week) = 1)",
            name="request_weeks",
        ),
        Index(
            "uq_capacity_workload_pending",
            "org_id",
            "employee_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )
    employee_id: UUID
    requested_percent: int
    start_week: date | None = None
    end_week: date | None = None


class CapacityDemand(TenantRecord, table=True):
    __tablename__ = "capacity_demand"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("requesting_department_id", "department"),
        tenant_fk("target_department_id", "department"),
        member_fk("requester_user_id"),
        CheckConstraint("headcount BETWEEN 1 AND 50", name="demand_headcount"),
        CheckConstraint(
            "allocation_percent BETWEEN 10 AND 100", name="demand_allocation"
        ),
        CheckConstraint("window_end >= window_start", name="demand_window"),
        CheckConstraint(
            "committed_end_date >= committed_start_date", name="demand_commitment"
        ),
        CheckConstraint(
            "length(description) BETWEEN 1 AND 2000", name="demand_description"
        ),
    )
    requesting_department_id: UUID
    requester_user_id: UUID
    target_department_id: UUID
    capability_key: str
    capability_name: str
    minimum_level: str | None = None
    headcount: int
    allocation_percent: int
    window_start: date
    window_end: date
    description: str = Field(max_length=2000)
    status: DemandStatus = Field(
        default=DemandStatus.SUBMITTED, sa_type=enum_type(DemandStatus)
    )
    committed_start_date: date | None = None
    committed_end_date: date | None = None
    revision: int = 1


class CapacityDemandEvent(TenantRecord, table=True):
    __tablename__ = "capacity_demand_event"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("demand_id", "capacity_demand"),
        member_fk("actor_user_id"),
        CheckConstraint("length(text) <= 2000", name="demand_event_text"),
        CheckConstraint(
            "via IN ('web', 'bot', 'feishu', 'mcp')", name="demand_event_via"
        ),
    )
    demand_id: UUID
    kind: DemandEventKind = Field(sa_type=enum_type(DemandEventKind))
    actor_user_id: UUID
    text: str = Field(max_length=2000)
    outcome: DemandOutcome | None = Field(
        default=None, sa_type=enum_type(DemandOutcome)
    )
    start_date: date | None = None
    end_date: date | None = None
    basis: dict[str, JsonValue] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    via: str


class CapacityAssignment(TenantRecord, table=True):
    __tablename__ = "capacity_assignment"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("demand_id", "capacity_demand"),
        tenant_fk("employee_id", "employee"),
        member_fk("assigned_by_user_id"),
        CheckConstraint(
            "allocation_percent BETWEEN 10 AND 100", name="assignment_allocation"
        ),
        CheckConstraint(
            "start_date IS NULL OR start_date <= due_date", name="assignment_dates"
        ),
        Index(
            "uq_capacity_assignment_active",
            "org_id",
            "demand_id",
            "employee_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
    demand_id: UUID
    employee_id: UUID
    allocation_percent: int
    start_date: date | None = None
    due_date: date
    status: AssignmentStatus = Field(
        default=AssignmentStatus.ACTIVE, sa_type=enum_type(AssignmentStatus)
    )
    assigned_by_user_id: UUID
    closed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class CapacityExtensionRequest(CapacityDecision, TenantRecord, table=True):
    __tablename__ = "capacity_extension_request"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("assignment_id", "capacity_assignment"),
        member_fk("decided_by_user_id"),
        member_fk("decided_for_user_id"),
        CheckConstraint("length(reason) BETWEEN 1 AND 500", name="extension_reason"),
        Index(
            "uq_capacity_extension_pending",
            "org_id",
            "assignment_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )
    assignment_id: UUID
    requested_due_date: date
