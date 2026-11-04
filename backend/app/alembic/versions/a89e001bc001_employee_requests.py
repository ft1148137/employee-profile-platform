"""Employee submissions, revisions and inbox.

Revision ID: a89e001bc001
Revises: a88e001bc001
"""
from alembic import op

revision = "a89e001bc001"
down_revision = "a88e001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE profile_change_request ADD COLUMN revision integer NOT NULL DEFAULT 1;
        ALTER TABLE profile_change_request ADD COLUMN submitted_via varchar(14)
            CHECK (submitted_via IN ('web','mcp','import','bot','admin_handover'));
        ALTER TABLE profile_change_request ADD COLUMN client_name varchar;
        ALTER TABLE profile_change_request ADD COLUMN submitted_at timestamptz;
        ALTER TABLE profile_change_request DROP CONSTRAINT profile_change_request_origin_check;
        ALTER TABLE profile_change_request ADD CONSTRAINT profile_change_request_origin_check
            CHECK (origin IN ('initial_sync','incremental_sync','conversation','manual','correction','import'));
        ALTER TABLE change_item ADD COLUMN owner_statement varchar;
        ALTER TABLE change_item ADD COLUMN objection varchar;
        CREATE TABLE inbox_item (
            id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
            created_at timestamptz NOT NULL, recipient_employee_id uuid NOT NULL,
            kind varchar(14) NOT NULL CHECK (kind IN ('initial_review','submitted','status_changed','question')),
            request_id uuid, question_id uuid, message varchar NOT NULL,
            read_at timestamptz, done_at timestamptz,
            UNIQUE (org_id, id),
            FOREIGN KEY (org_id, recipient_employee_id) REFERENCES employee(org_id, id),
            FOREIGN KEY (org_id, request_id) REFERENCES profile_change_request(org_id, id)
        );
        CREATE INDEX ix_inbox_item_org_id ON inbox_item(org_id);
        CREATE INDEX ix_inbox_recipient ON inbox_item(org_id,recipient_employee_id,created_at);
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE inbox_item;
        ALTER TABLE change_item DROP COLUMN objection;
        ALTER TABLE change_item DROP COLUMN owner_statement;
        UPDATE profile_change_request SET origin='manual' WHERE origin='import';
        ALTER TABLE profile_change_request DROP CONSTRAINT profile_change_request_origin_check;
        ALTER TABLE profile_change_request ADD CONSTRAINT profile_change_request_origin_check
            CHECK (origin IN ('initial_sync','incremental_sync','conversation','manual','correction'));
        ALTER TABLE profile_change_request DROP COLUMN submitted_at;
        ALTER TABLE profile_change_request DROP COLUMN client_name;
        ALTER TABLE profile_change_request DROP COLUMN submitted_via;
        ALTER TABLE profile_change_request DROP COLUMN revision;
    """)
