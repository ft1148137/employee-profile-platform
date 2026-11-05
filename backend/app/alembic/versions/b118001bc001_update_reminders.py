"""Periodic employee questions and update settings.

Revision ID: b118001bc001
Revises: b119001bc001
"""
from alembic import op
import sqlalchemy as sa

revision = "b118001bc001"
down_revision = "b119001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "update_setting",
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), primary_key=True),
        sa.Column("reminder_schedule", sa.String(), nullable=False),
        sa.Column("stale_after_months", sa.Integer(), nullable=False),
        sa.Column("next_reminder_at", sa.DateTime(timezone=True)),
        sa.Column("updated_by_user_id", sa.Uuid()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["org_id", "updated_by_user_id"], ["org_membership.org_id", "org_membership.user_id"]),
        sa.CheckConstraint("reminder_schedule IN ('quarterly', 'yearly', 'off')", name="ck_update_schedule"),
        sa.CheckConstraint("stale_after_months BETWEEN 6 AND 120", name="ck_update_stale_months"),
    )
    op.add_column("pending_question", sa.Column("kind", sa.String(), nullable=False, server_default="general"))
    op.create_check_constraint("questionkind", "pending_question", "kind IN ('conflict', 'stale', 'single_source', 'record', 'general')")
    op.add_column("pending_question", sa.Column("claim_id", sa.Uuid()))
    op.create_foreign_key("fk_question_claim", "pending_question", "profile_claim", ["org_id", "claim_id"], ["org_id", "id"])


def downgrade() -> None:
    op.drop_constraint("fk_question_claim", "pending_question", type_="foreignkey")
    op.drop_column("pending_question", "claim_id")
    op.drop_constraint("questionkind", "pending_question", type_="check")
    op.drop_column("pending_question", "kind")
    op.drop_table("update_setting")
