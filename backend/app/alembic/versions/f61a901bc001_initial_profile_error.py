"""Add initial profile inference error.

Revision ID: f61a901bc001
Revises: f73a901bc001
"""
from alembic import op
import sqlalchemy as sa

revision = "f61a901bc001"
down_revision = "f73a901bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profile_progress", sa.Column("inference_error", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("profile_progress", "inference_error")
