"""Transactional IM notification outbox."""
from alembic import op
import sqlalchemy as sa

revision = "b130001bc001"
down_revision = "b123001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "im_notification",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("inbox_item_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["org_id", "inbox_item_id"], ["inbox_item.org_id", "inbox_item.id"]),
        sa.ForeignKeyConstraint(["org_id", "employee_id"], ["employee.org_id", "employee.id"]),
        sa.UniqueConstraint("org_id", "inbox_item_id", "platform"),
        sa.CheckConstraint("platform IN ('telegram', 'feishu', 'wecom')"),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')"),
        sa.CheckConstraint("attempts BETWEEN 0 AND 3"),
    )
    op.create_index("ix_im_notification_org_id", "im_notification", ["org_id"])
    op.create_index("ix_im_notification_pending", "im_notification", ["org_id", "status", "created_at"])


def downgrade() -> None:
    op.drop_table("im_notification")
