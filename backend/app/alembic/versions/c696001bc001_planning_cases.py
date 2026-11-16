"""Planning cases, snapshots and immutable options.

Revision ID: c696001bc001
Revises: c693001bc001
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "c696001bc001"
down_revision = "c693001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "planning_case",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("demand_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["org_id", "demand_id"], ["capacity_demand.org_id", "capacity_demand.id"]),
        sa.ForeignKeyConstraint(["org_id", "created_by_user_id"], ["org_membership.org_id", "org_membership.user_id"]),
        sa.UniqueConstraint("org_id", "id"),
        sa.UniqueConstraint("org_id", "demand_id"),
    )
    op.create_index("ix_planning_case_org_id", "planning_case", ["org_id"])
    op.create_table(
        "planning_snapshot",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["org_id", "case_id"], ["planning_case.org_id", "planning_case.id"]),
        sa.ForeignKeyConstraint(["org_id", "created_by_user_id"], ["org_membership.org_id", "org_membership.user_id"]),
        sa.UniqueConstraint("org_id", "id"),
    )
    op.create_index("ix_planning_snapshot_org_id", "planning_snapshot", ["org_id"])
    op.create_table(
        "planning_option",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Enum("direct", "newcomer", "borrow", "delay", "reduce_scope", "phased", "custom", native_enum=False, create_constraint=True), nullable=False),
        sa.Column("params", JSONB(), nullable=False),
        sa.Column("verdict", sa.Enum("feasible", "conditional", "infeasible", "insufficient_data", native_enum=False, create_constraint=True), nullable=False),
        sa.Column("findings", JSONB(), nullable=False),
        sa.Column("summary", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["org_id", "snapshot_id"], ["planning_snapshot.org_id", "planning_snapshot.id"]),
        sa.UniqueConstraint("org_id", "id"),
    )
    op.create_index("ix_planning_option_org_id", "planning_option", ["org_id"])


def downgrade() -> None:
    for table in ("planning_option", "planning_snapshot", "planning_case"):
        op.drop_index(f"ix_{table}_org_id", table_name=table)
        op.drop_table(table)
