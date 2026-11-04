"""Encrypted bot history and admission leases.

Revision ID: b97e001bc001
Revises: b102001bc001
"""
from alembic import op

revision = "b97e001bc001"
down_revision = "b102001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE bot_setting (
            org_id uuid PRIMARY KEY REFERENCES organization(id),
            question_guide varchar NOT NULL DEFAULT '' CHECK (length(question_guide) <= 4000),
            retention_days integer NOT NULL DEFAULT 0 CHECK (retention_days BETWEEN 0 AND 30)
        );
        CREATE TABLE bot_session (
            id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
            employee_id uuid NOT NULL, channel varchar NOT NULL CHECK (channel = 'web'),
            started_at timestamptz NOT NULL, last_activity_at timestamptz NOT NULL,
            turn_count integer NOT NULL DEFAULT 0 CHECK (turn_count >= 0),
            request_ids uuid[] NOT NULL DEFAULT '{}', lease_until timestamptz,
            UNIQUE (org_id, id),
            FOREIGN KEY (org_id, employee_id) REFERENCES employee(org_id, id)
        );
        CREATE INDEX ix_bot_session_org_id ON bot_session(org_id);
        CREATE INDEX ix_bot_session_employee ON bot_session(org_id,employee_id);
        CREATE INDEX ix_bot_session_lease ON bot_session(org_id,lease_until);
        CREATE TABLE bot_message (
            id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
            session_id uuid NOT NULL, role varchar NOT NULL CHECK (role IN ('user','assistant')),
            ciphertext varchar NOT NULL, created_at timestamptz NOT NULL,
            FOREIGN KEY (org_id, session_id) REFERENCES bot_session(org_id, id) ON DELETE CASCADE
        );
        CREATE INDEX ix_bot_message_org_id ON bot_message(org_id);
        CREATE INDEX ix_bot_message_session_time ON bot_message(org_id,session_id,created_at);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE bot_message; DROP TABLE bot_session; DROP TABLE bot_setting;")
