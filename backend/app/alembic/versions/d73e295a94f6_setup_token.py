"""Add platform-level one-time setup tokens.

Revision ID: d73e295a94f6
Revises: b63e92d7f140
"""

import sqlalchemy as sa
from alembic import op

revision = "d73e295a94f6"
down_revision = "b63e92d7f140"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setup_token",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(13), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.CheckConstraint("purpose IN ('initial_setup', 'new_org')", name="setuppurpose"),
    )


def downgrade() -> None:
    op.drop_table("setup_token")
