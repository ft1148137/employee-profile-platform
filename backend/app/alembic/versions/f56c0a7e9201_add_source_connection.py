"""Add encrypted tenant source connections.

Revision ID: f56c0a7e9201
Revises: f58a901bc001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f56c0a7e9201"
down_revision = "f58a901bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_connection",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("secret_ciphertext", sa.String(), nullable=True),
        sa.Column("synced_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["org_id", "updated_by_user_id"], ["org_membership.org_id", "org_membership.user_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id"),
        sa.UniqueConstraint("org_id", "kind"),
    )
    op.create_index("ix_source_connection_org_id", "source_connection", ["org_id"])


def downgrade() -> None:
    op.drop_table("source_connection")
