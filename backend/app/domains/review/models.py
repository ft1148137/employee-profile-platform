from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.domains.base import TenantRecord


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
