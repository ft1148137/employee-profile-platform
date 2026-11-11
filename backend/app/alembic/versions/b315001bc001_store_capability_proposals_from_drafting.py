"""Store capability proposals from drafting

Revision ID: b315001bc001
Revises: b308001bc001
Create Date: 2026-11-11 14:42:20.763387

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

# revision identifiers, used by Alembic.
revision = 'b315001bc001'
down_revision = 'b308001bc001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('capability_proposal',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
    sa.Column('name_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('category', sqlmodel.sql.sqltypes.AutoString(length=40), nullable=True),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(length=300), nullable=False),
    sa.Column('reason', sqlmodel.sql.sqltypes.AutoString(length=300), nullable=False),
    sa.Column('status', sa.Enum('open', 'accepted', 'dismissed', name='proposalstatus', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['org_id', 'decided_by_user_id'], ['org_membership.org_id', 'org_membership.user_id']),
    sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'id')
    )
    op.create_index(op.f('ix_capability_proposal_org_id'), 'capability_proposal', ['org_id'], unique=False)
    op.create_index('uq_open_proposal_name', 'capability_proposal', ['org_id', 'name_key'], unique=True, postgresql_where=sa.text("status = 'open'"))
    op.create_table('capability_proposal_evidence',
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('proposal_id', sa.Uuid(), nullable=False),
    sa.Column('evidence_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['org_id', 'evidence_id'], ['evidence.org_id', 'evidence.id'], ),
    sa.ForeignKeyConstraint(['org_id', 'proposal_id'], ['capability_proposal.org_id', 'capability_proposal.id'], ),
    sa.PrimaryKeyConstraint('org_id', 'proposal_id', 'evidence_id')
    )


def downgrade() -> None:
    # Downgrade discards all proposals, decisions and their evidence links.
    op.drop_table("capability_proposal_evidence")
    op.drop_index("uq_open_proposal_name", table_name="capability_proposal")
    op.drop_index("ix_capability_proposal_org_id", table_name="capability_proposal")
    op.drop_table("capability_proposal")
