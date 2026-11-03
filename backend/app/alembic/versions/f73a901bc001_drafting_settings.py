"""Add automatic drafting preferences."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f73a901bc001"
down_revision = "f69a901bc002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("department", sa.Column("auto_draft_disabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("employee", sa.Column("auto_draft_opt_out", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "drafting_setting",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("excluded_capability_keys", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["org_id", "updated_by_user_id"], ["org_membership.org_id", "org_membership.user_id"]),
        sa.PrimaryKeyConstraint("org_id"),
    )


def downgrade() -> None:
    op.drop_table("drafting_setting")
    op.drop_column("employee", "auto_draft_opt_out")
    op.drop_column("department", "auto_draft_disabled")
