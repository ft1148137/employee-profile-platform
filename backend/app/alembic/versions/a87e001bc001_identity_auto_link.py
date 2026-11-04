"""Remember administrator opt-outs from automatic identity linking.

Revision ID: a87e001bc001
Revises: a88e001bc001
"""

import sqlalchemy as sa
from alembic import op

revision = "a87e001bc001"
down_revision = "a88e001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "external_identity",
        sa.Column("auto_link_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(sa.text("""
        UPDATE external_identity AS identity
        SET auto_link_blocked = true
        WHERE identity.employee_id IS NULL AND EXISTS (
            SELECT 1 FROM audit_event AS audit
            WHERE audit.org_id = identity.org_id
              AND audit.target_id = CAST(identity.id AS text)
              AND audit.target_type = 'external_identity'
              AND audit.action = 'external_identity.unlinked'
              AND audit.actor_type = 'user'
        )
    """))


def downgrade() -> None:
    op.drop_column("external_identity", "auto_link_blocked")
