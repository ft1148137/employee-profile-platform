"""Persist organization setup step decisions.

Revision ID: b63e92d7f140
Revises: a51b093e72c4
"""
from alembic import op
import sqlalchemy as sa

revision = "b63e92d7f140"
down_revision = "a51b093e72c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setup_step_state",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("step_key", sa.String(), nullable=False),
        sa.Column("status", sa.Enum("completed", "skipped", name="setupstatus", native_enum=False, create_constraint=True), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("org_id", "step_key"),
    )


def downgrade() -> None:
    op.drop_table("setup_step_state")
