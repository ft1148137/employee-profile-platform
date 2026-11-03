"""Add initial profile inference error.

Revision ID: f61a901bc001
Revises: 093e2d94e5aa
"""
from alembic import op
import sqlalchemy as sa

revision = "f61a901bc001"
down_revision = "093e2d94e5aa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profile_progress", sa.Column("inference_error", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("profile_progress", "inference_error")
