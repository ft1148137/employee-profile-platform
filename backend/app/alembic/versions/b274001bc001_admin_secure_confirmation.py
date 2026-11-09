"""Add high-risk confirmation and encrypted draft secrets.

Revision ID: b274001bc001
Revises: b278001bc001
"""
from alembic import op
import sqlalchemy as sa

revision = "b274001bc001"
down_revision = "b278001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("admin_change_set", sa.Column("confirmed_once_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("admin_change_set", sa.Column("secrets", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("admin_change_set", "secrets")
    op.drop_column("admin_change_set", "confirmed_once_at")
