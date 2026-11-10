"""Encrypt Pi conversation state alongside display messages.

Revision ID: b286001bc001
Revises: b274001bc001
"""

import sqlalchemy as sa
from alembic import op

revision = "b286001bc001"
down_revision = "b274001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("bot_session", "admin_assistant_session"):
        op.add_column(table, sa.Column("state_ciphertext", sa.String(), nullable=True))


def downgrade() -> None:
    for table in ("admin_assistant_session", "bot_session"):
        op.drop_column(table, "state_ciphertext")
