"""Add optional calendar schedules to source connections."""

import sqlalchemy as sa
from alembic import op

revision = "a88e001bc001"
down_revision = "a84e001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_connection",
        sa.Column("sync_schedule", sa.String(), nullable=False, server_default="off"),
    )
    op.add_column(
        "source_connection",
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("source_connection", "next_sync_at")
    op.drop_column("source_connection", "sync_schedule")
