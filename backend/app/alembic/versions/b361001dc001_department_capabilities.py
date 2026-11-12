"""Assign stable capability keys to departments."""

from alembic import op
import sqlalchemy as sa

revision = "b361001dc001"
down_revision = "b317001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "department_capability",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("capability_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("org_id", "department_id", "capability_key"),
        sa.ForeignKeyConstraint(
            ["org_id", "department_id"],
            ["department.org_id", "department.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
    )


def downgrade() -> None:
    # Downgrade discards every department-capability assignment; audit history remains.
    op.drop_table("department_capability")
