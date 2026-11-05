"""Time-bounded review delegation."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ExcludeConstraint

revision = "b126001bc001"
down_revision = "b127001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "review_delegation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("manager_user_id", sa.Uuid(), nullable=False),
        sa.Column("delegate_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        *(sa.ForeignKeyConstraint(["org_id", column], ["org_membership.org_id", "org_membership.user_id"])
          for column in ("manager_user_id", "delegate_user_id", "created_by_user_id")),
        sa.CheckConstraint("manager_user_id <> delegate_user_id", name="delegation_not_self"),
        sa.CheckConstraint("starts_at < ends_at", name="delegation_dates"),
        sa.CheckConstraint("created_by_user_id = manager_user_id", name="delegation_owner"),
        ExcludeConstraint(("org_id", "="), ("manager_user_id", "="),
                          (sa.text("tstzrange(starts_at, ends_at, '[)')"), "&&"),
                          where=sa.text("revoked_at IS NULL"), name="delegation_no_overlap"),
    )
    op.create_index("ix_review_delegation_org_id", "review_delegation", ["org_id"])


def downgrade() -> None:
    op.drop_table("review_delegation")
