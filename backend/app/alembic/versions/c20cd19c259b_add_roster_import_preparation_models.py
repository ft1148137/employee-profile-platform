"""Add roster import preparation models

Revision ID: c20cd19c259b
Revises: d73e295a94f6
Create Date: 2026-11-02 23:14:06.135506

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c20cd19c259b'
down_revision = 'd73e295a94f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('roster_import',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('filename', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('file_sha256', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('rows', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('column_mapping', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('issues', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('base_revision', sa.Integer(), nullable=False),
    sa.Column('uploaded_by_user_id', sa.Uuid(), nullable=False),
    sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('applied_by_user_id', sa.Uuid(), nullable=True),
    sa.CheckConstraint("status IN ('previewed', 'applied', 'stale')", name='ck_roster_status'),
    sa.ForeignKeyConstraint(['applied_by_user_id'], ['user.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
    sa.ForeignKeyConstraint(['uploaded_by_user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'id')
    )
    op.create_index(op.f('ix_roster_import_org_id'), 'roster_import', ['org_id'], unique=False)
    op.create_table('reporting_line',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('employee_id', sa.Uuid(), nullable=False),
    sa.Column('manager_employee_id', sa.Uuid(), nullable=False),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('roster_import_id', sa.Uuid(), nullable=True),
    sa.CheckConstraint('employee_id <> manager_employee_id', name='ck_reporting_not_self'),
    sa.ForeignKeyConstraint(['org_id', 'employee_id'], ['employee.org_id', 'employee.id'], ),
    sa.ForeignKeyConstraint(['org_id', 'manager_employee_id'], ['employee.org_id', 'employee.id'], ),
    sa.ForeignKeyConstraint(['org_id', 'roster_import_id'], ['roster_import.org_id', 'roster_import.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_reporting_line_org_id'), 'reporting_line', ['org_id'], unique=False)
    op.create_index('uq_current_reporting_line', 'reporting_line', ['org_id', 'employee_id'], unique=True, postgresql_where=sa.text('valid_to IS NULL'))
    op.drop_constraint(op.f('department_org_id_name_key'), 'department', type_='unique')
    op.create_unique_constraint('uq_department_path', 'department', ['org_id', 'parent_department_id', 'name'], postgresql_nulls_not_distinct=True)
    op.add_column('organization', sa.Column('roster_revision', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    duplicate = op.get_bind().execute(sa.text("""
        SELECT org_id, name FROM department
        GROUP BY org_id, name HAVING count(*) > 1 LIMIT 1
    """)).first()
    if duplicate:
        raise ValueError("Cannot downgrade: departments have duplicate names within an organization; rename them before downgrading")
    op.drop_column('organization', 'roster_revision')
    op.drop_constraint('uq_department_path', 'department', type_='unique')
    op.create_unique_constraint(op.f('department_org_id_name_key'), 'department', ['org_id', 'name'], postgresql_nulls_not_distinct=False)
    op.drop_index('uq_current_reporting_line', table_name='reporting_line', postgresql_where=sa.text('valid_to IS NULL'))
    op.drop_index(op.f('ix_reporting_line_org_id'), table_name='reporting_line')
    op.drop_table('reporting_line')
    op.drop_index(op.f('ix_roster_import_org_id'), table_name='roster_import')
    op.drop_table('roster_import')
