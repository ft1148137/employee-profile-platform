"""Review appeals without mutating published claims."""
from alembic import op
import sqlalchemy as sa

revision = "b127001bc001"
down_revision = "b123001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_appeal",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_by_user_id", sa.Uuid()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_note", sa.String()),
        sa.ForeignKeyConstraint(["org_id", "request_id"], ["profile_change_request.org_id", "profile_change_request.id"]),
        sa.ForeignKeyConstraint(["org_id", "item_id"], ["change_item.org_id", "change_item.id"]),
        sa.ForeignKeyConstraint(["org_id", "employee_id"], ["employee.org_id", "employee.id"]),
        sa.ForeignKeyConstraint(["org_id", "resolved_by_user_id"], ["org_membership.org_id", "org_membership.user_id"]),
        sa.CheckConstraint("status IN ('open', 'upheld', 'reopened')", name="appealstatus"),
    )
    op.create_index("ix_review_appeal_org_id", "review_appeal", ["org_id"])
    op.create_index("uq_open_appeal_item", "review_appeal", ["org_id", "item_id"], unique=True, postgresql_where=sa.text("status = 'open'"))


def downgrade() -> None:
    op.drop_table("review_appeal")
