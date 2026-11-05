"""Use tenant user identities for reviewers and inbox recipients.

Revision ID: b117002bc001
Revises: b117001bc001
"""
from alembic import op

revision = "b117002bc001"
down_revision = "b117001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE review_decision DROP CONSTRAINT review_decision_org_id_reviewer_membership_id_fkey;
        ALTER TABLE review_decision DROP CONSTRAINT review_decision_org_id_acting_for_membership_id_fkey;
        UPDATE review_decision d SET reviewer_membership_id = m.user_id
            FROM org_membership m WHERE m.org_id=d.org_id AND m.id=d.reviewer_membership_id;
        UPDATE review_decision d SET acting_for_membership_id = m.user_id
            FROM org_membership m WHERE m.org_id=d.org_id AND m.id=d.acting_for_membership_id;
        ALTER TABLE review_decision RENAME COLUMN reviewer_membership_id TO reviewer_user_id;
        ALTER TABLE review_decision RENAME COLUMN acting_for_membership_id TO acting_for_user_id;
        ALTER TABLE review_decision ADD CONSTRAINT fk_review_user
            FOREIGN KEY (org_id,reviewer_user_id) REFERENCES org_membership(org_id,user_id);
        ALTER TABLE review_decision ADD CONSTRAINT fk_review_acting_user
            FOREIGN KEY (org_id,acting_for_user_id) REFERENCES org_membership(org_id,user_id);
        ALTER TABLE inbox_item ADD COLUMN recipient_user_id uuid;
        ALTER TABLE inbox_item ALTER COLUMN recipient_employee_id DROP NOT NULL;
        ALTER TABLE inbox_item ADD CONSTRAINT fk_inbox_user
            FOREIGN KEY (org_id,recipient_user_id) REFERENCES org_membership(org_id,user_id);
        UPDATE inbox_item i SET recipient_user_id=m.user_id, recipient_employee_id=NULL
            FROM org_membership m WHERE i.org_id=m.org_id
            AND i.recipient_employee_id=m.employee_id AND i.kind='review_requested';
        ALTER TABLE inbox_item ADD CONSTRAINT inbox_one_recipient
            CHECK (num_nonnulls(recipient_employee_id,recipient_user_id)=1);
        CREATE INDEX ix_inbox_user ON inbox_item(org_id,recipient_user_id,created_at);
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM inbox_item WHERE recipient_user_id IS NOT NULL;
        ALTER TABLE inbox_item DROP CONSTRAINT inbox_one_recipient;
        ALTER TABLE inbox_item DROP COLUMN recipient_user_id;
        ALTER TABLE inbox_item ALTER COLUMN recipient_employee_id SET NOT NULL;
        ALTER TABLE review_decision DROP CONSTRAINT fk_review_user;
        ALTER TABLE review_decision DROP CONSTRAINT fk_review_acting_user;
        UPDATE review_decision d SET reviewer_user_id=m.id
            FROM org_membership m WHERE m.org_id=d.org_id AND m.user_id=d.reviewer_user_id;
        UPDATE review_decision d SET acting_for_user_id=m.id
            FROM org_membership m WHERE m.org_id=d.org_id AND m.user_id=d.acting_for_user_id;
        ALTER TABLE review_decision RENAME COLUMN reviewer_user_id TO reviewer_membership_id;
        ALTER TABLE review_decision RENAME COLUMN acting_for_user_id TO acting_for_membership_id;
        ALTER TABLE review_decision ADD CONSTRAINT review_decision_org_id_reviewer_membership_id_fkey
            FOREIGN KEY (org_id,reviewer_membership_id) REFERENCES org_membership(org_id,id);
        ALTER TABLE review_decision ADD CONSTRAINT review_decision_org_id_acting_for_membership_id_fkey
            FOREIGN KEY (org_id,acting_for_membership_id) REFERENCES org_membership(org_id,id);
    """)
