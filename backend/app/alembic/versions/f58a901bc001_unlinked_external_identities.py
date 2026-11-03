"""Allow observed external accounts before explicit employee linking.

Revision ID: f58a901bc001
Revises: f51c0a7e9201
"""
from alembic import op
import sqlalchemy as sa

revision = "f58a901bc001"
down_revision = "f51c0a7e9201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("external_identity", "employee_id", nullable=True)
    op.alter_column("external_identity", "linked_at", nullable=True)
    op.add_column("external_identity", sa.Column("email", sa.String(), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM external_identity WHERE employee_id IS NULL")
    op.execute("UPDATE external_identity SET linked_at = now() WHERE linked_at IS NULL")
    op.drop_column("external_identity", "email")
    op.alter_column("external_identity", "linked_at", nullable=False)
    op.alter_column("external_identity", "employee_id", nullable=False)
