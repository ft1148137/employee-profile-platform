"""Add capacity module foundation

Revision ID: c641001bc001
Revises: b622001dc001
Create Date: 2026-11-15 16:47:55.886754

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c641001bc001'
down_revision = 'b622001dc001'
branch_labels = None
depends_on = None
KINDS = "'initial_review','submitted','status_changed','question','dictionary_changed','review_requested','decided','admin_todos','profile_edited'"


def upgrade() -> None:
    op.drop_constraint("inbox_item_kind_check", "inbox_item")
    op.create_check_constraint("inbox_item_kind_check", "inbox_item", f"kind IN ({KINDS},'capacity')")
    op.create_table('capacity_demand',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('requesting_department_id', sa.Uuid(), nullable=False),
        sa.Column('requester_user_id', sa.Uuid(), nullable=False),
        sa.Column('target_department_id', sa.Uuid(), nullable=False),
        sa.Column('capability_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('capability_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('minimum_level', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('headcount', sa.Integer(), nullable=False),
        sa.Column('allocation_percent', sa.Integer(), nullable=False),
        sa.Column('window_start', sa.Date(), nullable=False),
        sa.Column('window_end', sa.Date(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(length=2000), nullable=False),
        sa.Column('status', sa.Enum('submitted', 'replied', 'accepted', 'withdrawn', name='demandstatus', native_enum=False, create_constraint=True), nullable=False),
        sa.Column('committed_start_date', sa.Date(), nullable=True),
        sa.Column('committed_end_date', sa.Date(), nullable=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.CheckConstraint('allocation_percent BETWEEN 10 AND 100', name='demand_allocation'),
        sa.CheckConstraint('committed_end_date >= committed_start_date', name='demand_commitment'),
        sa.CheckConstraint('headcount BETWEEN 1 AND 50', name='demand_headcount'),
        sa.CheckConstraint('length(description) BETWEEN 1 AND 2000', name='demand_description'),
        sa.CheckConstraint('window_end >= window_start', name='demand_window'),
        sa.ForeignKeyConstraint(['org_id', 'requester_user_id'], ['org_membership.org_id', 'org_membership.user_id'], ),
        sa.ForeignKeyConstraint(['org_id', 'requesting_department_id'], ['department.org_id', 'department.id'], ),
        sa.ForeignKeyConstraint(['org_id', 'target_department_id'], ['department.org_id', 'department.id'], ),
        sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'id')
        )
    op.create_index(op.f('ix_capacity_demand_org_id'), 'capacity_demand', ['org_id'], unique=False)
    op.create_table('capacity_setting',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('requester_policy', sa.Enum('everyone', 'managers', name='requesterpolicy', native_enum=False, create_constraint=True), nullable=False),
        sa.Column('updated_by_user_id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id', 'updated_by_user_id'], ['org_membership.org_id', 'org_membership.user_id'], ),
        sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id'),
        sa.UniqueConstraint('org_id', 'id')
        )
    op.create_index(op.f('ix_capacity_setting_org_id'), 'capacity_setting', ['org_id'], unique=False)
    op.create_table('capacity_workload',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('employee_id', sa.Uuid(), nullable=False),
        sa.Column('base_percent', sa.Integer(), nullable=False),
        sa.Column('updated_by_user_id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('base_percent BETWEEN 0 AND 200', name='workload_percent'),
        sa.ForeignKeyConstraint(['org_id', 'employee_id'], ['employee.org_id', 'employee.id'], ),
        sa.ForeignKeyConstraint(['org_id', 'updated_by_user_id'], ['org_membership.org_id', 'org_membership.user_id'], ),
        sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'employee_id'),
        sa.UniqueConstraint('org_id', 'id')
        )
    op.create_index(op.f('ix_capacity_workload_org_id'), 'capacity_workload', ['org_id'], unique=False)
    op.create_table('capacity_workload_request',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.Enum('pending', 'approved', 'returned', name='capacityrequeststatus', native_enum=False, create_constraint=True), nullable=False),
        sa.Column('reason', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column('decided_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('decided_for_user_id', sa.Uuid(), nullable=True),
        sa.Column('decision_note', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('employee_id', sa.Uuid(), nullable=False),
        sa.Column('requested_percent', sa.Integer(), nullable=False),
        sa.CheckConstraint('length(reason) BETWEEN 1 AND 500', name='workload_reason'),
        sa.CheckConstraint('requested_percent BETWEEN 0 AND 200', name='requested_percent'),
        sa.ForeignKeyConstraint(['org_id', 'decided_by_user_id'], ['org_membership.org_id', 'org_membership.user_id'], ),
        sa.ForeignKeyConstraint(['org_id', 'decided_for_user_id'], ['org_membership.org_id', 'org_membership.user_id'], ),
        sa.ForeignKeyConstraint(['org_id', 'employee_id'], ['employee.org_id', 'employee.id'], ),
        sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'id')
        )
    op.create_index(op.f('ix_capacity_workload_request_org_id'), 'capacity_workload_request', ['org_id'], unique=False)
    op.create_index('uq_capacity_workload_pending', 'capacity_workload_request', ['org_id', 'employee_id'], unique=True, postgresql_where=sa.text("status = 'pending'"))
    op.create_table('capacity_assignment',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('demand_id', sa.Uuid(), nullable=False),
        sa.Column('employee_id', sa.Uuid(), nullable=False),
        sa.Column('allocation_percent', sa.Integer(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('status', sa.Enum('active', 'done', 'cancelled', name='assignmentstatus', native_enum=False, create_constraint=True), nullable=False),
        sa.Column('assigned_by_user_id', sa.Uuid(), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('allocation_percent BETWEEN 10 AND 100', name='assignment_allocation'),
        sa.ForeignKeyConstraint(['org_id', 'assigned_by_user_id'], ['org_membership.org_id', 'org_membership.user_id'], ),
        sa.ForeignKeyConstraint(['org_id', 'demand_id'], ['capacity_demand.org_id', 'capacity_demand.id'], ),
        sa.ForeignKeyConstraint(['org_id', 'employee_id'], ['employee.org_id', 'employee.id'], ),
        sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'id')
        )
    op.create_index(op.f('ix_capacity_assignment_org_id'), 'capacity_assignment', ['org_id'], unique=False)
    op.create_index('uq_capacity_assignment_active', 'capacity_assignment', ['org_id', 'demand_id', 'employee_id'], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.create_table('capacity_demand_event',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('demand_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.Enum('submitted', 'replied', 'follow_up', 'accepted', 'withdrawn', 'commitment_changed', name='demandeventkind', native_enum=False, create_constraint=True), nullable=False),
        sa.Column('actor_user_id', sa.Uuid(), nullable=False),
        sa.Column('text', sqlmodel.sql.sqltypes.AutoString(length=2000), nullable=False),
        sa.Column('outcome', sa.Enum('can_do', 'partial', 'not_now', name='demandoutcome', native_enum=False, create_constraint=True), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('basis', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('via', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint("via IN ('web', 'bot', 'feishu', 'mcp')", name='demand_event_via'),
        sa.CheckConstraint('length(text) <= 2000', name='demand_event_text'),
        sa.ForeignKeyConstraint(['org_id', 'actor_user_id'], ['org_membership.org_id', 'org_membership.user_id'], ),
        sa.ForeignKeyConstraint(['org_id', 'demand_id'], ['capacity_demand.org_id', 'capacity_demand.id'], ),
        sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'id')
        )
    op.create_index(op.f('ix_capacity_demand_event_org_id'), 'capacity_demand_event', ['org_id'], unique=False)
    op.create_table('capacity_extension_request',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.Enum('pending', 'approved', 'returned', name='capacityrequeststatus', native_enum=False, create_constraint=True), nullable=False),
        sa.Column('reason', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column('decided_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('decided_for_user_id', sa.Uuid(), nullable=True),
        sa.Column('decision_note', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('assignment_id', sa.Uuid(), nullable=False),
        sa.Column('requested_due_date', sa.Date(), nullable=False),
        sa.CheckConstraint('length(reason) BETWEEN 1 AND 500', name='extension_reason'),
        sa.ForeignKeyConstraint(['org_id', 'assignment_id'], ['capacity_assignment.org_id', 'capacity_assignment.id'], ),
        sa.ForeignKeyConstraint(['org_id', 'decided_by_user_id'], ['org_membership.org_id', 'org_membership.user_id'], ),
        sa.ForeignKeyConstraint(['org_id', 'decided_for_user_id'], ['org_membership.org_id', 'org_membership.user_id'], ),
        sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'id')
        )
    op.create_index(op.f('ix_capacity_extension_request_org_id'), 'capacity_extension_request', ['org_id'], unique=False)
    op.create_index('uq_capacity_extension_pending', 'capacity_extension_request', ['org_id', 'assignment_id'], unique=True, postgresql_where=sa.text("status = 'pending'"))

def downgrade() -> None:
    op.execute("DELETE FROM im_notification WHERE (org_id, inbox_item_id) IN (SELECT org_id, id FROM inbox_item WHERE kind='capacity')")
    op.execute("DELETE FROM inbox_item WHERE kind='capacity'")
    op.drop_constraint("inbox_item_kind_check", "inbox_item")
    op.create_check_constraint("inbox_item_kind_check", "inbox_item", f"kind IN ({KINDS})")
    op.drop_index('uq_capacity_extension_pending', table_name='capacity_extension_request', postgresql_where=sa.text("status = 'pending'"))
    op.drop_index(op.f('ix_capacity_extension_request_org_id'), table_name='capacity_extension_request')
    op.drop_table('capacity_extension_request')
    op.drop_index(op.f('ix_capacity_demand_event_org_id'), table_name='capacity_demand_event')
    op.drop_table('capacity_demand_event')
    op.drop_index('uq_capacity_assignment_active', table_name='capacity_assignment', postgresql_where=sa.text("status = 'active'"))
    op.drop_index(op.f('ix_capacity_assignment_org_id'), table_name='capacity_assignment')
    op.drop_table('capacity_assignment')
    op.drop_index('uq_capacity_workload_pending', table_name='capacity_workload_request', postgresql_where=sa.text("status = 'pending'"))
    op.drop_index(op.f('ix_capacity_workload_request_org_id'), table_name='capacity_workload_request')
    op.drop_table('capacity_workload_request')
    op.drop_index(op.f('ix_capacity_workload_org_id'), table_name='capacity_workload')
    op.drop_table('capacity_workload')
    op.drop_index(op.f('ix_capacity_setting_org_id'), table_name='capacity_setting')
    op.drop_table('capacity_setting')
    op.drop_index(op.f('ix_capacity_demand_org_id'), table_name='capacity_demand')
    op.drop_table('capacity_demand')
