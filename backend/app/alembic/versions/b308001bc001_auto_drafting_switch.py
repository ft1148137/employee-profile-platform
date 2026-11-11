"""Replace department and capability exclusions with one opt-in switch."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b308001bc001"
down_revision = "b309001ef001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drafting_setting", sa.Column("auto_draft_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.drop_column("drafting_setting", "excluded_capability_keys")
    op.drop_column("department", "auto_draft_disabled")


def downgrade() -> None:
    # Removed per-capability and per-department settings cannot be restored.
    op.add_column("department", sa.Column("auto_draft_disabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("drafting_setting", sa.Column("excluded_capability_keys", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.drop_column("drafting_setting", "auto_draft_enabled")
