"""Enforce append-only evidence and employee-scoped content uniqueness.

Revision ID: f52a917c0b32
Revises: f55a901bc001
"""

from alembic import op

revision = "f52a917c0b32"
down_revision = "f55a901bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_evidence_content", "evidence", type_="unique")
    op.create_unique_constraint(
        "uq_evidence_content", "evidence",
        ["org_id", "source", "source_ref", "subject_employee_id", "content_hash"],
    )
    op.execute("""
        CREATE FUNCTION guard_evidence_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF (to_jsonb(NEW) - 'status' - 'status_reason') IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'status_reason') THEN
                RAISE EXCEPTION 'Evidence content and attribution are immutable';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                OLD.status = 'active' AND NEW.status IN ('retracted', 'superseded')
            ) THEN
                RAISE EXCEPTION 'Invalid evidence status transition';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER evidence_immutable BEFORE UPDATE ON evidence
        FOR EACH ROW EXECUTE FUNCTION guard_evidence_update();

        CREATE FUNCTION guard_evidence_delete() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF current_setting('app.allow_evidence_delete', true) IS DISTINCT FROM 'on' THEN
                RAISE EXCEPTION 'Evidence cannot be deleted';
            END IF;
            RETURN OLD;
        END;
        $$;
        CREATE TRIGGER evidence_no_delete BEFORE DELETE ON evidence
        FOR EACH ROW EXECUTE FUNCTION guard_evidence_delete();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER evidence_immutable ON evidence")
    op.execute("DROP TRIGGER evidence_no_delete ON evidence")
    op.execute("DROP FUNCTION guard_evidence_update()")
    op.execute("DROP FUNCTION guard_evidence_delete()")
    op.drop_constraint("uq_evidence_content", "evidence", type_="unique")
    # Fails if different employees now share the same source/reference/content;
    # PostgreSQL rolls back this migration rather than deleting any evidence.
    op.create_unique_constraint(
        "uq_evidence_content", "evidence",
        ["org_id", "source", "source_ref", "content_hash"],
    )
