"""Capacity projects, roles, dependencies, and reason codes.

Revision ID: c832001bc001
Revises: c782001bc001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c832001bc001"
down_revision = "c782001bc001"
branch_labels = None
depends_on = None

REASON_TABLES = (
    ("capacity_approval", "approval_reason_code"),
    ("capacity_demand_event", "demand_event_reason_code"),
)


def upgrade() -> None:
    op.create_table(
        "capacity_project",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "org_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False
        ),
        sa.Column("requesting_department_id", sa.Uuid(), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False),
        sa.UniqueConstraint("org_id", "id"),
        sa.ForeignKeyConstraint(
            ["org_id", "requesting_department_id"],
            ["department.org_id", "department.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "requester_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        sa.CheckConstraint("length(title) BETWEEN 1 AND 100", name="project_title"),
        sa.CheckConstraint("length(description) <= 2000", name="project_description"),
    )
    op.create_index("ix_capacity_project_org_id", "capacity_project", ["org_id"])
    op.add_column("capacity_demand", sa.Column("project_id", sa.Uuid()))
    op.add_column("capacity_demand", sa.Column("role_name", sa.String(50)))
    op.add_column(
        "capacity_demand",
        sa.Column(
            "extra_capabilities",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_foreign_key(
        "fk_demand_project",
        "capacity_demand",
        "capacity_project",
        ["org_id", "project_id"],
        ["org_id", "id"],
    )
    op.create_index(
        "ix_capacity_demand_project", "capacity_demand", ["org_id", "project_id"]
    )
    op.create_check_constraint(
        "demand_project_role",
        "capacity_demand",
        "project_id IS NULL OR (role_name IS NOT NULL AND length(role_name) BETWEEN 1 AND 50)",
    )
    op.create_table(
        "capacity_demand_dependency",
        sa.Column("org_id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("demand_id", sa.Uuid(), primary_key=True),
        sa.Column("depends_on_demand_id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"], ["capacity_project.org_id", "capacity_project.id"]
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "demand_id"], ["capacity_demand.org_id", "capacity_demand.id"]
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "depends_on_demand_id"],
            ["capacity_demand.org_id", "capacity_demand.id"],
        ),
        sa.CheckConstraint(
            "demand_id <> depends_on_demand_id", name="dependency_not_self"
        ),
    )
    op.create_index(
        "ix_capacity_demand_dependency_project",
        "capacity_demand_dependency",
        ["org_id", "project_id"],
    )
    for table, constraint in REASON_TABLES:
        op.add_column(table, sa.Column("reason_code", sa.String()))
        op.create_check_constraint(
            constraint,
            table,
            "reason_code IN ('capacity', 'skill', 'timing', 'priority', 'scope', 'other')",
        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE capacity_approval, capacity_demand, capacity_demand_dependency, "
            "capacity_demand_event, capacity_project IN ACCESS EXCLUSIVE MODE"
        )
    )
    blockers = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM capacity_project) OR "
            "EXISTS (SELECT 1 FROM capacity_demand WHERE project_id IS NOT NULL) OR "
            "EXISTS (SELECT 1 FROM capacity_demand_dependency) AS projects, "
            "EXISTS (SELECT 1 FROM capacity_approval WHERE reason_code IS NOT NULL) OR "
            "EXISTS (SELECT 1 FROM capacity_demand_event WHERE reason_code IS NOT NULL) AS reasons"
        )
    ).one()
    if blockers.projects or blockers.reasons:
        raise RuntimeError("存在项目需求、依赖或已记录的原因代码，无法降级并保留数据")

    for table, constraint in REASON_TABLES:
        op.drop_constraint(constraint, table, type_="check")
        op.drop_column(table, "reason_code")
    op.drop_table("capacity_demand_dependency")
    op.drop_constraint("demand_project_role", "capacity_demand", type_="check")
    op.drop_constraint("fk_demand_project", "capacity_demand", type_="foreignkey")
    op.drop_index("ix_capacity_demand_project", table_name="capacity_demand")
    for column in ("extra_capabilities", "role_name", "project_id"):
        op.drop_column("capacity_demand", column)
    op.drop_table("capacity_project")
