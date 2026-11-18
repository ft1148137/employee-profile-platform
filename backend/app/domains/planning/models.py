from enum import StrEnum
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.domains.base import TenantRecord, enum_type, tenant_fk
from app.domains.capacity.models import member_fk


class OptionKind(StrEnum):
    DIRECT = "direct"
    NEWCOMER = "newcomer"
    BORROW = "borrow"
    DELAY = "delay"
    REDUCE_SCOPE = "reduce_scope"
    PHASED = "phased"
    OPTIMIZED = "optimized"
    CUSTOM = "custom"


class PlanVerdict(StrEnum):
    FEASIBLE = "feasible"
    CONDITIONAL = "conditional"
    INFEASIBLE = "infeasible"
    INSUFFICIENT_DATA = "insufficient_data"


class PlanningCase(TenantRecord, table=True):
    __tablename__ = "planning_case"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "demand_id"),
        tenant_fk("demand_id", "capacity_demand"),
        member_fk("created_by_user_id"),
    )
    demand_id: UUID
    created_by_user_id: UUID


class PlanningSnapshot(TenantRecord, table=True):
    __tablename__ = "planning_snapshot"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("case_id", "planning_case"),
        member_fk("created_by_user_id"),
    )
    case_id: UUID
    data: dict[str, JsonValue] = Field(sa_column=Column(JSONB, nullable=False))
    fingerprint: str
    created_by_user_id: UUID


class PlanningOption(TenantRecord, table=True):
    __tablename__ = "planning_option"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("snapshot_id", "planning_snapshot"),
    )
    snapshot_id: UUID
    kind: OptionKind = Field(sa_type=enum_type(OptionKind))
    params: dict[str, JsonValue] = Field(sa_column=Column(JSONB, nullable=False))
    verdict: PlanVerdict = Field(sa_type=enum_type(PlanVerdict))
    findings: list[dict[str, JsonValue]] = Field(
        sa_column=Column(JSONB, nullable=False)
    )
    summary: dict[str, JsonValue] = Field(sa_column=Column(JSONB, nullable=False))
