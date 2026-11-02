"""Track login attempts for shared database rate limiting.

Revision ID: b62c194f83d5
Revises: a51b093e72c4
"""

import sqlalchemy as sa
from alembic import op

revision = "b62c194f83d5"
down_revision = "a51b093e72c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_attempt",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("identifier_norm", sa.String(512), nullable=False),
        sa.Column("ip", sa.String(255), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_login_attempt_identifier_created", "login_attempt", ["identifier_norm", "created_at"])
    op.create_index("ix_login_attempt_ip_created", "login_attempt", ["ip", "created_at"])


def downgrade() -> None:
    op.drop_table("login_attempt")
