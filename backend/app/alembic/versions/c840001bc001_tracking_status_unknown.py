"""Allow an unknown assignment tracking state."""

import sqlalchemy as sa
from alembic import op

revision = "c840001bc001"
down_revision = "c839001bc001"
branch_labels = None
depends_on = None

TABLE = "capacity_assignment"
CONSTRAINT = "assignment_tracking_state"
PREVIOUS = "'no_tasks', 'on_track', 'tasks_done', 'overdue', 'handed_over'"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(
        CONSTRAINT, TABLE, f"tracking_state IN ({PREVIOUS}, 'status_unknown')"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE capacity_assignment IN ACCESS EXCLUSIVE MODE")
    )
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM capacity_assignment"
            " WHERE tracking_state = 'status_unknown')"
        )
    ).scalar_one():
        raise RuntimeError("存在关联任务状态待确认的分配，无法降级并保留数据")
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, f"tracking_state IN ({PREVIOUS})")
