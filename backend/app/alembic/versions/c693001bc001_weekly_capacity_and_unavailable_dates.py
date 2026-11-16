"""Weekly capacity and unavailable dates

Revision ID: c693001bc001
Revises: c641001bc001
Create Date: 2026-11-16 09:19:26.078499

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c693001bc001'
down_revision = 'c641001bc001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('capacity_unavailability',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('employee_id', sa.Uuid(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('created_by_user_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('end_date >= start_date', name='unavailability_dates'),
    sa.ForeignKeyConstraint(['org_id', 'created_by_user_id'], ['org_membership.org_id', 'org_membership.user_id'], ),
    sa.ForeignKeyConstraint(['org_id', 'employee_id'], ['employee.org_id', 'employee.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'id')
    )
    op.create_index(op.f('ix_capacity_unavailability_org_id'), 'capacity_unavailability', ['org_id'], unique=False)
    op.create_table('capacity_week',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('employee_id', sa.Uuid(), nullable=False),
    sa.Column('week_start', sa.Date(), nullable=False),
    sa.Column('other_percent', sa.Integer(), nullable=False),
    sa.Column('confirmed_by_user_id', sa.Uuid(), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('extract(isodow from week_start) = 1', name='week_monday'),
    sa.CheckConstraint('other_percent BETWEEN 0 AND 200', name='week_percent'),
    sa.ForeignKeyConstraint(['org_id', 'confirmed_by_user_id'], ['org_membership.org_id', 'org_membership.user_id'], ),
    sa.ForeignKeyConstraint(['org_id', 'employee_id'], ['employee.org_id', 'employee.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'employee_id', 'week_start'),
    sa.UniqueConstraint('org_id', 'id')
    )
    op.create_index(op.f('ix_capacity_week_org_id'), 'capacity_week', ['org_id'], unique=False)
    op.execute("""
        INSERT INTO capacity_week
            (id, created_at, org_id, employee_id, week_start, other_percent, confirmed_by_user_id, confirmed_at)
        SELECT id, created_at, org_id, employee_id,
            date_trunc('week', updated_at AT TIME ZONE 'UTC')::date,
            base_percent, updated_by_user_id, updated_at
        FROM capacity_workload
    """)
    op.drop_table("capacity_workload")
    op.add_column("capacity_assignment", sa.Column("start_date", sa.Date(), nullable=True))
    op.create_check_constraint("assignment_dates", "capacity_assignment", "start_date IS NULL OR start_date <= due_date")
    op.add_column("capacity_workload_request", sa.Column("start_week", sa.Date(), nullable=True))
    op.add_column("capacity_workload_request", sa.Column("end_week", sa.Date(), nullable=True))
    op.create_check_constraint("request_weeks", "capacity_workload_request",
        "(start_week IS NULL AND end_week IS NULL) OR "
        "(start_week IS NOT NULL AND end_week IS NOT NULL AND start_week <= end_week "
        "AND extract(isodow from start_week) = 1 AND extract(isodow from end_week) = 1)")


def downgrade() -> None:
    # Downgrade loses weekly detail and unavailable dates; only each employee's latest week survives.
    op.create_table('capacity_workload',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('org_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('employee_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('base_percent', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('updated_by_user_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.CheckConstraint('base_percent >= 0 AND base_percent <= 200', name=op.f('workload_percent')),
    sa.ForeignKeyConstraint(['org_id', 'employee_id'], ['employee.org_id', 'employee.id'], name=op.f('capacity_workload_org_id_employee_id_fkey')),
    sa.ForeignKeyConstraint(['org_id', 'updated_by_user_id'], ['org_membership.org_id', 'org_membership.user_id'], name=op.f('capacity_workload_org_id_updated_by_user_id_fkey')),
    sa.ForeignKeyConstraint(['org_id'], ['organization.id'], name=op.f('capacity_workload_org_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('capacity_workload_pkey')),
    sa.UniqueConstraint('org_id', 'employee_id', name=op.f('capacity_workload_org_id_employee_id_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('org_id', 'id', name=op.f('capacity_workload_org_id_id_key'), postgresql_include=[], postgresql_nulls_not_distinct=False)
    )
    op.create_index(op.f('ix_capacity_workload_org_id'), 'capacity_workload', ['org_id'], unique=False)
    op.execute("""
        INSERT INTO capacity_workload
            (id, created_at, org_id, employee_id, base_percent, updated_by_user_id, updated_at)
        SELECT DISTINCT ON (org_id, employee_id)
            id, created_at, org_id, employee_id, other_percent, confirmed_by_user_id, confirmed_at
        FROM capacity_week ORDER BY org_id, employee_id, week_start DESC
    """)
    op.drop_constraint("request_weeks", "capacity_workload_request")
    op.drop_column("capacity_workload_request", "end_week")
    op.drop_column("capacity_workload_request", "start_week")
    op.drop_constraint("assignment_dates", "capacity_assignment")
    op.drop_column("capacity_assignment", "start_date")
    op.drop_table("capacity_week")
    op.drop_table("capacity_unavailability")
