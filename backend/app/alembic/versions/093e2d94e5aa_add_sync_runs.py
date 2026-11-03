"""add sync runs

Revision ID: 093e2d94e5aa
Revises: f69a901bc002
Create Date: 2026-11-03 17:24:54.893074

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '093e2d94e5aa'
down_revision = 'f69a901bc002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('sync_run',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('connection_id', sa.Uuid(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('trigger', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('cursor', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('counts', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('input', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('requested_by_user_id', sa.Uuid(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id', 'connection_id'], ['source_connection.org_id', 'source_connection.id'], ),
    sa.ForeignKeyConstraint(['org_id', 'requested_by_user_id'], ['org_membership.org_id', 'org_membership.user_id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'id')
    )
    op.create_index(op.f('ix_sync_run_org_id'), 'sync_run', ['org_id'], unique=False)
    op.create_index('uq_sync_run_active', 'sync_run', ['org_id', 'connection_id'], unique=True, postgresql_where=sa.text("status IN ('queued', 'running')"))


def downgrade() -> None:
    op.drop_table("sync_run")
