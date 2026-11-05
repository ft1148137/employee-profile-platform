"""Review decisions and atomic publication.

Revision ID: b117001bc001
Revises: a98e001bc001
"""
from alembic import op

revision = "b117001bc001"
down_revision = "a98e001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE review_decision (
            id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
            created_at timestamptz NOT NULL, request_id uuid NOT NULL,
            reviewer_membership_id uuid, acting_for_membership_id uuid,
            via varchar(6) NOT NULL CHECK (via IN ('web','mcp','im','policy','sync')),
            client_name varchar, comment varchar, request_revision integer NOT NULL,
            UNIQUE (org_id,id),
            FOREIGN KEY (org_id,request_id) REFERENCES profile_change_request(org_id,id),
            FOREIGN KEY (org_id,reviewer_membership_id) REFERENCES org_membership(org_id,id),
            FOREIGN KEY (org_id,acting_for_membership_id) REFERENCES org_membership(org_id,id),
            CONSTRAINT review_decision_actor CHECK (
                (via IN ('policy','sync')) = (reviewer_membership_id IS NULL)
            )
        );
        CREATE INDEX ix_review_decision_org_id ON review_decision(org_id);
        ALTER TABLE profile_change_request ADD COLUMN decided_at timestamptz;
        ALTER TABLE profile_change_request ADD COLUMN parent_request_id uuid;
        ALTER TABLE profile_change_request ADD CONSTRAINT fk_request_parent
            FOREIGN KEY (org_id,parent_request_id) REFERENCES profile_change_request(org_id,id);
        ALTER TABLE change_item ADD COLUMN decision varchar(17)
            CHECK (decision IN ('approved','changes_requested','rejected'));
        ALTER TABLE change_item ADD COLUMN decision_reason varchar;
        ALTER TABLE change_item ADD COLUMN review_decision_id uuid;
        ALTER TABLE change_item ADD COLUMN published_claim_id uuid;
        ALTER TABLE change_item ADD CONSTRAINT fk_item_decision
            FOREIGN KEY (org_id,review_decision_id) REFERENCES review_decision(org_id,id);
        ALTER TABLE change_item ADD CONSTRAINT fk_item_published
            FOREIGN KEY (org_id,published_claim_id) REFERENCES profile_claim(org_id,id);
        ALTER TABLE inbox_item DROP CONSTRAINT inbox_item_kind_check;
        ALTER TABLE inbox_item ADD CONSTRAINT inbox_item_kind_check CHECK (
            kind IN ('initial_review','submitted','status_changed','question','dictionary_changed','review_requested','decided')
        );
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM inbox_item WHERE kind IN ('review_requested','decided');
        ALTER TABLE inbox_item DROP CONSTRAINT inbox_item_kind_check;
        ALTER TABLE inbox_item ADD CONSTRAINT inbox_item_kind_check CHECK (
            kind IN ('initial_review','submitted','status_changed','question','dictionary_changed')
        );
        ALTER TABLE change_item DROP COLUMN published_claim_id;
        ALTER TABLE change_item DROP COLUMN review_decision_id;
        ALTER TABLE change_item DROP COLUMN decision_reason;
        ALTER TABLE change_item DROP COLUMN decision;
        ALTER TABLE profile_change_request DROP COLUMN parent_request_id;
        ALTER TABLE profile_change_request DROP COLUMN decided_at;
        DROP TABLE review_decision;
    """)
