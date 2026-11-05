"""Channel conversations and bound one-time IM actions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b141001bc001"
down_revision = "b123001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("bot_session_channel_check", "bot_session", type_="check")
    op.create_check_constraint("ck_bot_session_channel", "bot_session", "channel IN ('web', 'telegram', 'feishu', 'wecom')")
    op.add_column("bot_session", sa.Column("external_chat_id", sa.String()))
    op.add_column("bot_session", sa.Column("bot_identity", sa.String()))
    op.add_column("bot_session", sa.Column("pending_question_id", sa.Uuid()))
    op.create_foreign_key("fk_bot_session_pending_question", "bot_session", "pending_question", ["org_id", "pending_question_id"], ["org_id", "id"])
    op.create_table("im_action",
        sa.Column("token", sa.String(22), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("bot_identity", sa.String(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("external_chat_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("action", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("platform IN ('telegram', 'feishu', 'wecom')"),
        sa.ForeignKeyConstraint(["org_id", "employee_id"], ["employee.org_id", "employee.id"]),
        sa.ForeignKeyConstraint(["org_id", "session_id"], ["bot_session.org_id", "bot_session.id"]),
    )
    op.create_index("ix_im_action_org_id", "im_action", ["org_id"])


def downgrade() -> None:
    op.drop_table("im_action")
    # IM histories have no corresponding web session after downgrade.
    op.execute("DELETE FROM bot_session WHERE channel <> 'web'")
    op.drop_constraint("fk_bot_session_pending_question", "bot_session", type_="foreignkey")
    for column in ("pending_question_id", "bot_identity", "external_chat_id"):
        op.drop_column("bot_session", column)
    op.drop_constraint("ck_bot_session_channel", "bot_session", type_="check")
    op.create_check_constraint("bot_session_channel_check", "bot_session", "channel = 'web'")
