"""Add encrypted organization model settings.

Revision ID: f55a901bc001
Revises: e35a91c7d024
"""
from alembic import op
import sqlalchemy as sa

revision = "f55a901bc001"
down_revision = "e35a91c7d024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_setting",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=True),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("api_key_ciphertext", sa.String(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["org_id", "updated_by_user_id"], ["org_membership.org_id", "org_membership.user_id"]),
        sa.PrimaryKeyConstraint("org_id"),
    )


def downgrade() -> None:
    op.drop_table("model_setting")
