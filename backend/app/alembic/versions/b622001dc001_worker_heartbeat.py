"""Add singleton worker process state, deliberately without tenant org_id."""

import sqlalchemy as sa
from alembic import op

revision = "b622001dc001"
down_revision = "b617001dc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeat",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("beat_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_worker_heartbeat_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeat")
