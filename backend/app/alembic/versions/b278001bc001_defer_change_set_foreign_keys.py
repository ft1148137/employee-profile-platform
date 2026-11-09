"""Check draft change-set references at commit, after the model turn.

Revision ID: b278001bc001
Revises: b263001bc001
"""
from alembic import op

revision = "b278001bc001"
down_revision = "b263001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE admin_change_set
            ALTER CONSTRAINT admin_change_set_org_id_fkey DEFERRABLE INITIALLY DEFERRED,
            ALTER CONSTRAINT admin_change_set_org_id_session_id_fkey DEFERRABLE INITIALLY DEFERRED,
            ALTER CONSTRAINT admin_change_set_org_id_undo_of_id_fkey DEFERRABLE INITIALLY DEFERRED,
            ALTER CONSTRAINT admin_change_set_org_id_created_by_user_id_fkey DEFERRABLE INITIALLY DEFERRED;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE admin_change_set
            ALTER CONSTRAINT admin_change_set_org_id_fkey NOT DEFERRABLE INITIALLY IMMEDIATE,
            ALTER CONSTRAINT admin_change_set_org_id_session_id_fkey NOT DEFERRABLE INITIALLY IMMEDIATE,
            ALTER CONSTRAINT admin_change_set_org_id_undo_of_id_fkey NOT DEFERRABLE INITIALLY IMMEDIATE,
            ALTER CONSTRAINT admin_change_set_org_id_created_by_user_id_fkey NOT DEFERRABLE INITIALLY IMMEDIATE;
    """)
