"""Add Procrastinate 3.10.0 schema.

Revision ID: 6c2d9e0f4a81
Revises: 84c2e7d91a60
"""

from pathlib import Path

from alembic import op

revision = "6c2d9e0f4a81"
down_revision = "84c2e7d91a60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = Path(__file__).with_name("procrastinate_3_10_0.sql").read_text()
    op.get_bind().exec_driver_sql(schema, execution_options={"no_parameters": True})


def downgrade() -> None:
    schema = Path(__file__).with_name("procrastinate_3_10_0_down.sql").read_text()
    op.get_bind().exec_driver_sql(schema, execution_options={"no_parameters": True})
