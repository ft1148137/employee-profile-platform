"""Versioned organization approval policies."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b122001bc001"
down_revision = "b125001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_policy",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("require_manager", sa.Boolean(), nullable=False),
        sa.Column("category_overrides", postgresql.JSONB(), nullable=False),
        sa.Column("owner_review_days", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("org_id", "version"),
        sa.ForeignKeyConstraint(["org_id", "created_by_user_id"], ["org_membership.org_id", "org_membership.user_id"]),
        sa.CheckConstraint("version > 0", name="approval_policy_version"),
        sa.CheckConstraint("owner_review_days BETWEEN 1 AND 90", name="approval_policy_days"),
    )
    op.create_index("ix_approval_policy_org_id", "approval_policy", ["org_id"])


def downgrade() -> None:
    op.drop_table("approval_policy")
