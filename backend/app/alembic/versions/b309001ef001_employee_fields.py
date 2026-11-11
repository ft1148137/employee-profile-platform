"""Add opt-in fixed employee work attributes.

Revision ID: b309001ef001
Revises: b283001bc001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b309001ef001"
down_revision = "b283001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employee", sa.Column("job_family", sa.String(50), nullable=True))
    op.add_column("employee", sa.Column("work_location", sa.String(50), nullable=True))
    op.add_column("employee", sa.Column("hire_date", sa.Date(), nullable=True))
    op.add_column("organization", sa.Column("employee_fields", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))


def downgrade() -> None:
    # Downgrade discards the three work attributes and organization field switches.
    op.drop_column("organization", "employee_fields")
    for field in ("hire_date", "work_location", "job_family"):
        op.drop_column("employee", field)
