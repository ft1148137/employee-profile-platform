"""Append versioned enterprise drafting notes."""

from alembic import op
import sqlalchemy as sa

revision = "b317001bc001"
down_revision = "b313001ca001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drafting_note",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(2000), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("org_id", "version"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        sa.CheckConstraint("version >= 1"),
        sa.CheckConstraint("length(text) <= 2000"),
    )


def downgrade() -> None:
    # Downgrade discards all note versions; audit history remains.
    op.drop_table("drafting_note")
