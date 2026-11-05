from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlmodel import Field

from app.domains.base import TenantRecord, enum_type, tenant_fk


class AppealStatus(StrEnum):
    OPEN = "open"
    UPHELD = "upheld"
    REOPENED = "reopened"


class ReviewAppeal(TenantRecord, table=True):
    __tablename__ = "review_appeal"
    __table_args__ = (
        tenant_fk("request_id", "profile_change_request"),
        tenant_fk("item_id", "change_item"),
        tenant_fk("employee_id", "employee"),
        ForeignKeyConstraint(
            ["org_id", "resolved_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        Index(
            "uq_open_appeal_item",
            "org_id",
            "item_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )
    request_id: UUID
    item_id: UUID
    employee_id: UUID
    reason: str
    status: AppealStatus = Field(
        default=AppealStatus.OPEN, sa_type=enum_type(AppealStatus)
    )
    resolved_by_user_id: UUID | None = None
    resolved_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    resolution_note: str | None = None


class ReviewDelegation(TenantRecord, table=True):
    __tablename__ = "review_delegation"
    __table_args__ = (
        *(
            ForeignKeyConstraint(
                ["org_id", column],
                ["org_membership.org_id", "org_membership.user_id"],
            )
            for column in ("manager_user_id", "delegate_user_id", "created_by_user_id")
        ),
        CheckConstraint(
            "manager_user_id <> delegate_user_id", name="delegation_not_self"
        ),
        CheckConstraint("starts_at < ends_at", name="delegation_dates"),
        CheckConstraint(
            "created_by_user_id = manager_user_id", name="delegation_owner"
        ),
        ExcludeConstraint(
            ("org_id", "="),
            ("manager_user_id", "="),
            (text("tstzrange(starts_at, ends_at, '[)')"), "&&"),
            where=text("revoked_at IS NULL"),
            name="delegation_no_overlap",
        ),
    )
    manager_user_id: UUID
    delegate_user_id: UUID
    created_by_user_id: UUID
    starts_at: datetime = Field(sa_type=DateTime(timezone=True))
    ends_at: datetime = Field(sa_type=DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class ApprovalPolicy(TenantRecord, table=True):
    __tablename__ = "approval_policy"
    __table_args__ = (
        UniqueConstraint("org_id", "version"),
        ForeignKeyConstraint(
            ["org_id", "created_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        CheckConstraint("version > 0", name="approval_policy_version"),
        CheckConstraint(
            "owner_review_days BETWEEN 1 AND 90", name="approval_policy_days"
        ),
    )
    version: int
    require_manager: bool = True
    category_overrides: dict[str, bool] = Field(default_factory=dict, sa_type=JSONB)
    owner_review_days: int = 14
    created_by_user_id: UUID
