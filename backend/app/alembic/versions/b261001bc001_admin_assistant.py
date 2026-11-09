"""Private administrator conversations.

Revision ID: b261001bc001
Revises: b129001bc001
"""
from alembic import op

revision = "b261001bc001"
down_revision = "b129001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE admin_assistant_session (
            id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
            user_id uuid NOT NULL, started_at timestamptz NOT NULL,
            last_activity_at timestamptz NOT NULL,
            turn_count integer NOT NULL DEFAULT 0 CHECK (turn_count >= 0),
            lease_until timestamptz, UNIQUE (org_id, id),
            FOREIGN KEY (org_id, user_id) REFERENCES org_membership(org_id, user_id)
        );
        CREATE INDEX ix_admin_assistant_session_org_id ON admin_assistant_session(org_id);
        CREATE INDEX ix_admin_assistant_session_lease ON admin_assistant_session(org_id, lease_until);
        CREATE TABLE admin_assistant_message (
            id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
            session_id uuid NOT NULL, role varchar NOT NULL CHECK (role IN ('user','assistant')),
            ciphertext varchar NOT NULL, created_at timestamptz NOT NULL,
            FOREIGN KEY (org_id, session_id) REFERENCES admin_assistant_session(org_id, id) ON DELETE CASCADE
        );
        CREATE INDEX ix_admin_assistant_message_org_id ON admin_assistant_message(org_id);
        CREATE INDEX ix_admin_assistant_message_session_time ON admin_assistant_message(org_id, session_id, created_at);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE admin_assistant_message; DROP TABLE admin_assistant_session;")
