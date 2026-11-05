"""Tenant-scoped encrypted IM settings."""
from alembic import op
import sqlalchemy as sa

revision = "b119001bc001"
down_revision = "a98e001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "im_setting",
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), primary_key=True),
        sa.Column("platform", sa.String(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("credentials", sa.String()),
        sa.Column("email_auto_bind", sa.Boolean(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("platform IN ('feishu', 'wecom', 'telegram')"),
        sa.ForeignKeyConstraint(["org_id", "updated_by_user_id"], ["org_membership.org_id", "org_membership.user_id"]),
    )


def downgrade() -> None:
    op.drop_table("im_setting")
