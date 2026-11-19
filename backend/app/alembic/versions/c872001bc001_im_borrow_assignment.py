"""Retain the original assignment for borrow notification authorization."""

import sqlalchemy as sa
from alembic import op

revision = "c872001bc001"
down_revision = "c840001bc001"
branch_labels = None
depends_on = None

TABLE = "im_notification"
COLUMN = "borrow_assignment_id"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
