"""Preserve template capability display order.

Revision ID: 7d3e0f1a5b92
Revises: 6c2d9e0f4a81
"""
from alembic import op
import sqlalchemy as sa

revision = "7d3e0f1a5b92"
down_revision = "6c2d9e0f4a81"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("capability_definition", sa.Column("position", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("capability_definition", "position")
