"""Employee pending questions and tenant-safe inbox references.

Revision ID: a95e001bc001
Revises: b97e001bc001
"""
from alembic import op

revision = "a95e001bc001"
down_revision = "b97e001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pending_question (
            id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
            created_at timestamptz NOT NULL, target_employee_id uuid NOT NULL,
            capability_definition_id uuid, question varchar NOT NULL, reason varchar NOT NULL,
            status varchar(9) NOT NULL CHECK (status IN ('open','answered','dismissed')),
            answer_evidence_id uuid, answered_at timestamptz,
            UNIQUE (org_id, id),
            FOREIGN KEY (org_id, target_employee_id) REFERENCES employee(org_id, id),
            FOREIGN KEY (org_id, capability_definition_id) REFERENCES capability_definition(org_id, id),
            FOREIGN KEY (org_id, answer_evidence_id) REFERENCES evidence(org_id, id)
        );
        CREATE INDEX ix_pending_question_org_id ON pending_question(org_id);
        CREATE INDEX ix_question_target ON pending_question(org_id,target_employee_id,created_at);
        ALTER TABLE inbox_item ADD CONSTRAINT fk_inbox_question
            FOREIGN KEY (org_id, question_id) REFERENCES pending_question(org_id, id);
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE inbox_item DROP CONSTRAINT fk_inbox_question;
        DELETE FROM inbox_item WHERE question_id IS NOT NULL;
        DROP TABLE pending_question;
    """)
