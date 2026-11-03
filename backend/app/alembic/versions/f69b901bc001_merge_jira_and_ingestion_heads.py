"""Merge Jira and ingestion heads

Revision ID: f69b901bc001
Revises: f56c0a7e9201, f58a901bc001
Create Date: 2026-11-03 16:25:22.608286

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = 'f69b901bc001'
down_revision = ('f56c0a7e9201', 'f58a901bc001')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
