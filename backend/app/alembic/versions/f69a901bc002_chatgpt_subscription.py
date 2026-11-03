"""Store encrypted subscription sessions and model pause metadata."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f69a901bc002"
down_revision = "f58a901bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("chatgpt_auth_ciphertext", "chatgpt_login_ciphertext", "pause_reason"):
        op.add_column("model_setting", sa.Column(name, sa.String(), nullable=True))
    op.add_column("model_setting", sa.Column("reasoning_effort", sa.String(10), nullable=True))
    op.add_column("model_setting", sa.Column("chatgpt_account", postgresql.JSONB(), nullable=True))
    for name in ("chatgpt_login_expires_at", "chatgpt_login_polled_at", "paused_until"):
        op.add_column("model_setting", sa.Column(name, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for name in ("chatgpt_auth_ciphertext", "chatgpt_login_ciphertext", "pause_reason", "reasoning_effort", "chatgpt_account", "chatgpt_login_expires_at", "chatgpt_login_polled_at", "paused_until"):
        op.drop_column("model_setting", name)
