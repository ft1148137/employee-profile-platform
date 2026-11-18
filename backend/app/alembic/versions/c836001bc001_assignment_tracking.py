"""Track tasks linked to capacity assignments."""

import sqlalchemy as sa
from alembic import op

revision = "c836001bc001"
down_revision = "c832001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("capacity_assignment", sa.Column("tracking_state", sa.String()))
    op.add_column(
        "capacity_assignment",
        sa.Column("tracking_changed_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "assignment_tracking_state",
        "capacity_assignment",
        "tracking_state IN ('no_tasks', 'on_track', 'tasks_done', 'overdue', 'handed_over')",
    )
    op.create_table(
        "capacity_assignment_task",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "org_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("linked_by_user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "assignment_id"],
            ["capacity_assignment.org_id", "capacity_assignment.id"],
        ),
        sa.ForeignKeyConstraint(["org_id", "task_id"], ["task.org_id", "task.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "linked_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        sa.UniqueConstraint("org_id", "assignment_id", "task_id"),
    )
    op.create_index(
        "ix_capacity_assignment_task_org_id", "capacity_assignment_task", ["org_id"]
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE capacity_assignment, capacity_assignment_task IN ACCESS EXCLUSIVE MODE"
        )
    )
    if connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM capacity_assignment_task)")
    ).scalar_one():
        raise RuntimeError("存在已关联的分配任务，无法降级并保留数据")
    op.drop_table("capacity_assignment_task")
    op.drop_constraint(
        "assignment_tracking_state", "capacity_assignment", type_="check"
    )
    op.drop_column("capacity_assignment", "tracking_changed_at")
    op.drop_column("capacity_assignment", "tracking_state")
