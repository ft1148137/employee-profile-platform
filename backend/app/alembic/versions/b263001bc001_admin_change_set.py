"""Administrator change sets.

Revision ID: b263001bc001
Revises: b261001bc001
"""
from alembic import op

revision = "b263001bc001"
down_revision = "b261001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE admin_change_set (
            id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
            session_id uuid NOT NULL, created_by_user_id uuid NOT NULL,
            status varchar NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','applied','discarded','stale','undone')),
            revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
            items jsonb NOT NULL CHECK (jsonb_typeof(items) = 'array'
                AND jsonb_array_length(items) BETWEEN 1 AND 50),
            applied_at timestamptz, undo_of_id uuid,
            created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
            UNIQUE (org_id, id),
            FOREIGN KEY (org_id, session_id) REFERENCES admin_assistant_session(org_id, id),
            FOREIGN KEY (org_id, created_by_user_id) REFERENCES org_membership(org_id, user_id),
            FOREIGN KEY (org_id, undo_of_id) REFERENCES admin_change_set(org_id, id)
        );
        CREATE INDEX ix_admin_change_set_org_id ON admin_change_set(org_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE admin_change_set")
