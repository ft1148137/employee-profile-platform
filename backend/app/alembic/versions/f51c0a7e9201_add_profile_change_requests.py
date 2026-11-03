"""Separate proposed changes from published claims.

Revision ID: f51c0a7e9201
Revises: f55a901bc001

Unversioned candidate/pending_employee/pending_manager/rejected claims become
manual draft/pending_owner/pending_review/rejected requests. Versioned claims stay.
Downgrade restores nonterminal proposals with an `after` to claims; approved,
changes_requested and withdrawn requests are excluded. Existing claims become
accepted. IDs/timestamps, judgment fields including confidence, explanation,
generated_by and evidence relations survive roundtrip. Nonempty evidence notes
abort upgrade. Modify restores supersedes from its target, add restores NULL.
Normalization is intentional: retained claims pointing to moved claims lose that
pointer; moved claims with no pointer or a noncurrent/moved predecessor point to
the latest retained current claim (or NULL). These pointers are not recoverable.
"""
from alembic import op

revision = "f51c0a7e9201"
down_revision = "f55a901bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE profile_change_request (
            id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
            created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
            subject_employee_id uuid NOT NULL, base_version_id uuid,
            origin varchar(16) NOT NULL CHECK (origin IN ('initial_sync','incremental_sync','conversation','manual','correction')),
            status varchar(17) NOT NULL CHECK (status IN ('draft','pending_owner','pending_review','approved','changes_requested','rejected','withdrawn')),
            author jsonb NOT NULL, generated_by jsonb NOT NULL,
            UNIQUE (org_id, id),
            FOREIGN KEY (org_id, subject_employee_id) REFERENCES employee(org_id, id),
            FOREIGN KEY (org_id, base_version_id) REFERENCES profile_version(org_id, id)
        );
        CREATE INDEX ix_profile_change_request_org_id ON profile_change_request (org_id);
        CREATE INDEX ix_request_employee_status ON profile_change_request (org_id, subject_employee_id, status);
        CREATE UNIQUE INDEX uq_request_initial_sync ON profile_change_request (org_id, subject_employee_id) WHERE origin = 'initial_sync';
        CREATE TABLE change_item (
            id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id), created_at timestamptz NOT NULL,
            request_id uuid NOT NULL, position integer NOT NULL,
            op varchar(15) NOT NULL CHECK (op IN ('add','modify','retract','attach_evidence')),
            capability_definition_id uuid NOT NULL, target_claim_id uuid,
            after jsonb, explanation varchar, uncertainty varchar,
            UNIQUE (org_id, id), UNIQUE (request_id, position),
            FOREIGN KEY (org_id, request_id) REFERENCES profile_change_request(org_id, id),
            FOREIGN KEY (org_id, capability_definition_id) REFERENCES capability_definition(org_id, id),
            FOREIGN KEY (org_id, target_claim_id) REFERENCES profile_claim(org_id, id),
            CONSTRAINT change_item_target CHECK ((op = 'add') = (target_claim_id IS NULL)),
            CONSTRAINT change_item_after CHECK ((op = 'retract' AND after IS NULL) OR (op IN ('add','modify') AND after IS NOT NULL) OR op = 'attach_evidence')
        );
        CREATE INDEX ix_change_item_org_id ON change_item (org_id);
        CREATE TABLE change_item_evidence (
            org_id uuid NOT NULL REFERENCES organization(id), item_id uuid NOT NULL, evidence_id uuid NOT NULL,
            relation varchar(11) NOT NULL CHECK (relation IN ('supports','contradicts','context')),
            PRIMARY KEY (item_id, evidence_id),
            FOREIGN KEY (org_id, item_id) REFERENCES change_item(org_id, id),
            FOREIGN KEY (org_id, evidence_id) REFERENCES evidence(org_id, id)
        );
        CREATE INDEX ix_change_item_evidence_org_id ON change_item_evidence (org_id);
        CREATE TEMP TABLE moving_claims ON COMMIT DROP AS
            SELECT c.* FROM profile_claim c WHERE review_status <> 'accepted'
            AND NOT EXISTS (SELECT 1 FROM profile_version v,
                jsonb_array_elements(COALESCE(v.snapshot->'claims', '[]'::jsonb)) s
                WHERE v.org_id = c.org_id AND s->>'id' = c.id::text);
        DO $$ DECLARE bad_claim uuid; BEGIN
            SELECT e.claim_id INTO bad_claim FROM claim_evidence e
                JOIN moving_claims m ON m.org_id=e.org_id AND m.id=e.claim_id
                WHERE e.note IS NOT NULL LIMIT 1;
            IF bad_claim IS NOT NULL THEN
                RAISE EXCEPTION 'Cannot migrate claim %: claim_evidence.note is not NULL', bad_claim;
            END IF;
        END $$;
        INSERT INTO profile_change_request
            SELECT c.id, c.org_id, c.created_at, c.created_at, c.subject_employee_id,
                (SELECT v.id FROM profile_version v WHERE v.org_id = c.org_id AND v.subject_employee_id = c.subject_employee_id ORDER BY revision DESC LIMIT 1),
                'manual', CASE review_status WHEN 'candidate' THEN 'draft' WHEN 'pending_employee' THEN 'pending_owner' WHEN 'pending_manager' THEN 'pending_review' ELSE 'rejected' END,
                c.generated_by, c.generated_by FROM moving_claims c;
        INSERT INTO change_item
            SELECT c.id, c.org_id, c.created_at, c.id, 0,
                CASE WHEN target.id IS NULL THEN 'add' ELSE 'modify' END,
                c.capability_definition_id, target.id,
                jsonb_build_object('statement', c.statement, 'level', c.level, 'assessment_status', c.assessment_status, 'confidence', c.confidence), c.explanation, NULL
            FROM moving_claims c LEFT JOIN LATERAL (
                SELECT p.id FROM profile_claim p WHERE p.org_id = c.org_id
                AND p.subject_employee_id = c.subject_employee_id AND p.capability_definition_id = c.capability_definition_id
                AND NOT EXISTS (SELECT 1 FROM moving_claims m WHERE m.id = p.id)
                AND NOT EXISTS (SELECT 1 FROM profile_claim next WHERE next.org_id = p.org_id AND next.supersedes_claim_id = p.id AND next.capability_definition_id = p.capability_definition_id AND NOT EXISTS (SELECT 1 FROM moving_claims m WHERE m.id = next.id))
                ORDER BY p.created_at DESC, p.id DESC LIMIT 1
            ) target ON true;
        INSERT INTO change_item_evidence SELECT e.org_id, e.claim_id, e.evidence_id, e.relation
            FROM claim_evidence e JOIN moving_claims m ON m.org_id=e.org_id AND m.id = e.claim_id;
        UPDATE profile_claim SET supersedes_claim_id = NULL WHERE supersedes_claim_id IN (SELECT id FROM moving_claims);
        DELETE FROM claim_evidence WHERE claim_id IN (SELECT id FROM moving_claims);
        DELETE FROM profile_claim WHERE id IN (SELECT id FROM moving_claims);
        ALTER TABLE profile_claim DROP COLUMN review_status;
        ALTER TABLE profile_progress DROP COLUMN review_status;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE profile_claim ADD COLUMN review_status varchar(16) NOT NULL DEFAULT 'accepted'
            CHECK (review_status IN ('candidate','pending_employee','pending_manager','accepted','rejected'));
        ALTER TABLE profile_progress ADD COLUMN review_status varchar(16) NOT NULL DEFAULT 'none'
            CHECK (review_status IN ('none','pending_employee','pending_manager','published','disputed'));
        UPDATE profile_progress p SET review_status = CASE
            WHEN EXISTS (SELECT 1 FROM profile_change_request r WHERE r.org_id=p.org_id AND r.subject_employee_id=p.employee_id AND r.status='pending_owner') THEN 'pending_employee'
            WHEN EXISTS (SELECT 1 FROM profile_change_request r WHERE r.org_id=p.org_id AND r.subject_employee_id=p.employee_id AND r.status='pending_review') THEN 'pending_manager'
            WHEN EXISTS (SELECT 1 FROM (
                SELECT DISTINCT ON (c.org_id,c.subject_employee_id,c.capability_definition_id) c.* FROM profile_claim c
                WHERE NOT EXISTS (SELECT 1 FROM profile_claim n WHERE n.org_id=c.org_id AND n.supersedes_claim_id=c.id AND n.capability_definition_id=c.capability_definition_id)
                ORDER BY c.org_id,c.subject_employee_id,c.capability_definition_id,c.created_at DESC,c.id DESC
            ) c WHERE c.org_id=p.org_id AND c.subject_employee_id=p.employee_id AND c.assessment_status='disputed') THEN 'disputed'
            WHEN EXISTS (SELECT 1 FROM profile_version v WHERE v.org_id=p.org_id AND v.subject_employee_id=p.employee_id) THEN 'published'
            ELSE 'none' END;
        INSERT INTO profile_claim (id,org_id,created_at,subject_employee_id,capability_definition_id,statement,level,assessment_status,review_status,confidence,explanation,generated_by,supersedes_claim_id)
            SELECT i.id,i.org_id,i.created_at,r.subject_employee_id,i.capability_definition_id,i.after->>'statement',i.after->>'level',i.after->>'assessment_status',
                CASE r.status WHEN 'draft' THEN 'candidate' WHEN 'pending_owner' THEN 'pending_employee' WHEN 'pending_review' THEN 'pending_manager' ELSE 'rejected' END,
                i.after->>'confidence',i.explanation,CASE WHEN r.generated_by='{}'::jsonb THEN r.author ELSE r.generated_by END,CASE WHEN i.op='modify' THEN i.target_claim_id ELSE NULL END
            FROM change_item i JOIN profile_change_request r ON r.org_id=i.org_id AND r.id=i.request_id
            WHERE i.after IS NOT NULL AND r.status IN ('draft','pending_owner','pending_review','rejected');
        INSERT INTO claim_evidence (org_id,claim_id,evidence_id,relation)
            SELECT e.org_id,e.item_id,e.evidence_id,e.relation FROM change_item_evidence e
            JOIN profile_claim c ON c.org_id=e.org_id AND c.id=e.item_id;
        DROP TABLE change_item_evidence;
        DROP TABLE change_item;
        DROP TABLE profile_change_request;
    """)
