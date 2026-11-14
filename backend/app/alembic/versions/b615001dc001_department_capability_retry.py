"""Cool down failed department capability generation for 24 hours."""

import sqlalchemy as sa
from alembic import op

revision = "b615001dc001"
down_revision = "b489001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "department",
        sa.Column("capability_failed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("department", "capability_failed_at")
