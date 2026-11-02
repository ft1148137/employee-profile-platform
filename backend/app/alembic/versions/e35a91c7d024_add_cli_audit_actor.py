"""Allow CLI audit actors.

Revision ID: e35a91c7d024
Revises: c20cd19c259b
"""

from alembic import op
import sqlalchemy as sa

revision = "e35a91c7d024"
down_revision = "c20cd19c259b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("actorkind", "audit_event", type_="check")
    op.create_check_constraint(
        "actorkind", "audit_event",
        "actor_type IN ('user', 'system_worker', 'agent', 'seed', 'cli')",
    )


def downgrade() -> None:
    op.execute("LOCK TABLE audit_event IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().execute(
        sa.text("SELECT 1 FROM audit_event WHERE actor_type = 'cli' LIMIT 1")
    ).first():
        raise ValueError(
            "Cannot downgrade: CLI audit events exist; downgrading would invalidate "
            "the append-only audit trail"
        )
    op.drop_constraint("actorkind", "audit_event", type_="check")
    op.create_check_constraint(
        "actorkind", "audit_event",
        "actor_type IN ('user', 'system_worker', 'agent', 'seed')",
    )
