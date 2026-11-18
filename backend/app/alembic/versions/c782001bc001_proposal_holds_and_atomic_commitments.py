"""Proposal holds and atomic commitments

Revision ID: c782001bc001
Revises: c696001bc001
Downgrade rejects any M9 proposal, approval, linked assignment, or proposal demand
event records, as well as multiple active segments, before any schema or data
changes; only empty or pre-M9 compatible databases can downgrade.

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c782001bc001"
down_revision = "c696001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capacity_proposal",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("demand_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "committed",
                "failed",
                "withdrawn",
                name="proposalstatus",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("fail_reason", sa.String(), nullable=True),
        sa.Column("source_option_id", sa.Uuid(), nullable=False),
        sa.Column("replaces_proposal_id", sa.Uuid(), nullable=True),
        sa.Column("demand_revision", sa.Integer(), nullable=False),
        sa.Column("proposed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("hold_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "(status = 'failed' AND fail_reason IS NOT NULL AND fail_reason IN ('rejected', 'expired', 'recheck_failed', 'borrow_declined')) OR (status <> 'failed' AND fail_reason IS NULL)",
            name="proposal_failure",
        ),
        sa.CheckConstraint("kind IN ('initial', 'change')", name="proposal_kind"),
        sa.ForeignKeyConstraint(
            ["org_id", "demand_id"],
            ["capacity_demand.org_id", "capacity_demand.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "proposed_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "replaces_proposal_id"],
            ["capacity_proposal.org_id", "capacity_proposal.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id"),
    )
    op.create_index(
        op.f("ix_capacity_proposal_org_id"),
        "capacity_proposal",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "uq_capacity_proposal_pending",
        "capacity_proposal",
        ["org_id", "demand_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "capacity_proposal_line",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=True),
        sa.Column("lender_department_id", sa.Uuid(), nullable=True),
        sa.Column("allocation_percent", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("outside_basis", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "allocation_percent BETWEEN 10 AND 100", name="proposal_line_allocation"
        ),
        sa.CheckConstraint(
            "employee_id IS NOT NULL OR lender_department_id IS NOT NULL",
            name="proposal_line_subject",
        ),
        sa.CheckConstraint("start_date <= end_date", name="proposal_line_dates"),
        sa.ForeignKeyConstraint(
            ["org_id", "employee_id"],
            ["employee.org_id", "employee.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "lender_department_id"],
            ["department.org_id", "department.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "proposal_id"],
            ["capacity_proposal.org_id", "capacity_proposal.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id"),
        sa.UniqueConstraint("org_id", "proposal_id", "id"),
    )
    op.create_index(
        op.f("ix_capacity_proposal_line_org_id"),
        "capacity_proposal_line",
        ["org_id"],
        unique=False,
    )
    op.create_table(
        "capacity_approval",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("line_id", sa.Uuid(), nullable=True),
        sa.Column("party", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("via", sa.String(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')", name="approval_decision"
        ),
        sa.CheckConstraint(
            "length(reason) <= 500 AND (decision <> 'rejected' OR (reason IS NOT NULL AND length(trim(reason)) > 0))",
            name="approval_reason",
        ),
        sa.CheckConstraint(
            "party IN ('resource_manager', 'requester', 'lender_manager', 'employee')",
            name="approval_party",
        ),
        sa.CheckConstraint(
            "via IN ('web', 'bot', 'feishu', 'mcp')", name="approval_via"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "proposal_id", "line_id"],
            [
                "capacity_proposal_line.org_id",
                "capacity_proposal_line.proposal_id",
                "capacity_proposal_line.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "proposal_id"],
            ["capacity_proposal.org_id", "capacity_proposal.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id"),
        sa.UniqueConstraint(
            "proposal_id",
            "party",
            "line_id",
            name="uq_capacity_approval_party",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_capacity_approval_org_id"),
        "capacity_approval",
        ["org_id"],
        unique=False,
    )

    op.add_column(
        "capacity_assignment", sa.Column("proposal_line_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_assignment_proposal_line",
        "capacity_assignment",
        "capacity_proposal_line",
        ["org_id", "proposal_line_id"],
        ["org_id", "id"],
    )
    op.drop_index("uq_capacity_assignment_active", table_name="capacity_assignment")
    op.create_index(
        "uq_capacity_assignment_active",
        "capacity_assignment",
        ["org_id", "demand_id", "employee_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND proposal_line_id IS NULL"),
    )
    op.create_index(
        "uq_capacity_assignment_proposal_line",
        "capacity_assignment",
        ["org_id", "proposal_line_id"],
        unique=True,
        postgresql_where=sa.text("proposal_line_id IS NOT NULL"),
    )
    op.drop_constraint("demandeventkind", "capacity_demand_event", type_="check")
    op.create_check_constraint(
        "demandeventkind",
        "capacity_demand_event",
        "kind IN ('submitted','replied','follow_up','accepted','withdrawn','commitment_changed','committed','proposal_failed')",
    )


def downgrade() -> None:
    connection = op.get_bind()
    # Block concurrent writers across all M9-touched tables before checking.
    connection.execute(
        sa.text(
            "LOCK TABLE capacity_assignment, capacity_proposal, capacity_proposal_line, capacity_approval, capacity_demand_event IN ACCESS EXCLUSIVE MODE"
        )
    )
    groups = (
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM (SELECT org_id,demand_id,employee_id FROM capacity_assignment WHERE status='active' GROUP BY org_id,demand_id,employee_id HAVING count(*) > 1) AS segments"
            )
        )
        or 0
    )
    proposals = connection.scalar(sa.text("SELECT count(*) FROM capacity_proposal")) or 0
    proposal_lines = (
        connection.scalar(sa.text("SELECT count(*) FROM capacity_proposal_line")) or 0
    )
    approvals = connection.scalar(sa.text("SELECT count(*) FROM capacity_approval")) or 0
    linked_assignments = (
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM capacity_assignment WHERE proposal_line_id IS NOT NULL"
            )
        )
        or 0
    )
    proposal_events = (
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM capacity_demand_event WHERE kind IN ('committed', 'proposal_failed')"
            )
        )
        or 0
    )
    blockers: list[str] = []
    if groups:
        blockers.append(f"有 {groups} 组同一员工的多段进行中分配")
    history_parts: list[str] = []
    if proposals:
        history_parts.append(f"capacity_proposal={proposals}")
    if proposal_lines:
        history_parts.append(f"capacity_proposal_line={proposal_lines}")
    if approvals:
        history_parts.append(f"capacity_approval={approvals}")
    if linked_assignments:
        history_parts.append(
            f"capacity_assignment.proposal_line_id={linked_assignments}"
        )
    if proposal_events:
        history_parts.append(
            f"capacity_demand_event(committed/proposal_failed)={proposal_events}"
        )
    if history_parts:
        blockers.append(
            "存在会被 M9 降级删除或解绑的协商与审批历史（"
            + "、".join(history_parts)
            + "）"
        )
    if blockers:
        raise RuntimeError("；".join(blockers) + "，请先人工处理再降级")
    op.drop_constraint(
        "fk_assignment_proposal_line", "capacity_assignment", type_="foreignkey"
    )
    op.drop_index(
        "uq_capacity_assignment_proposal_line", table_name="capacity_assignment"
    )
    op.drop_index("uq_capacity_assignment_active", table_name="capacity_assignment")
    op.drop_column("capacity_assignment", "proposal_line_id")
    op.create_index(
        "uq_capacity_assignment_active",
        "capacity_assignment",
        ["org_id", "demand_id", "employee_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_constraint("demandeventkind", "capacity_demand_event", type_="check")
    op.create_check_constraint(
        "demandeventkind",
        "capacity_demand_event",
        "kind IN ('submitted','replied','follow_up','accepted','withdrawn','commitment_changed')",
    )
    for table in ("capacity_approval", "capacity_proposal_line", "capacity_proposal"):
        op.drop_table(table)
