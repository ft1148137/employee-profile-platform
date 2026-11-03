import json
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from uuid import uuid7

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from app.core.db import engine
from app.domains.audit.models import ActorKind, AuditEvent
from app.domains.evidence.models import (
    Evidence,
    EvidenceKind,
    EvidenceStatus,
    Visibility,
)
from app.domains.evidence.service import (
    DuplicateEvidenceContent,
    InactiveEvidence,
    append_evidence,
    retract_evidence,
    supersede_evidence,
)
from app.domains.org.models import Employee, Organization
from tests.utils.org import delete_test_organization


@pytest.fixture
def evidence() -> Generator[tuple[Session, Evidence]]:
    with Session(engine) as session:
        org = Organization(slug=str(uuid7()), name="Synthetic evidence tests")
        session.add(org)
        session.flush()
        employee = Employee(org_id=org.id, display_name="Synthetic employee")
        session.add(employee)
        session.flush()
        original, _ = append_evidence(
            session,
            org_id=org.id,
            subject_employee_id=employee.id,
            kind=EvidenceKind.WORK_RECORD,
            source="jira",
            source_ref="SYN-1",
            title="Original",
            content={"private": "original body"},
            visibility=Visibility.RESTRICTED,
            provenance={"synthetic": True},
        )
        yield session, original
        session.rollback()


def correct(
    session: Session, original: Evidence, body: str = "corrected body"
) -> Evidence:
    return supersede_evidence(
        session,
        original=original,
        content={"private": body},
        title="Correction",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        event_id=None,
        provenance={"correction": True},
        reason="Synthetic correction",
        actor_type=ActorKind.SYSTEM_WORKER,
        actor_id="test-worker",
    )


def retract(session: Session, original: Evidence) -> None:
    retract_evidence(
        session,
        evidence=original,
        reason="Synthetic retraction",
        actor_type=ActorKind.USER,
        actor_id="test-user",
    )


def audits(session: Session, original: Evidence) -> list[AuditEvent]:
    return list(
        session.exec(select(AuditEvent).where(AuditEvent.org_id == original.org_id))
    )


def test_correction_chain_and_duplicate_history(
    evidence: tuple[Session, Evidence],
) -> None:
    session, original = evidence
    assert correct(session, original, "original body").id == original.id
    assert audits(session, original) == []
    correction = correct(session, original)
    latest = correct(session, correction, "latest body")
    assert correction.supersedes_evidence_id == original.id
    assert latest.supersedes_evidence_id == correction.id
    assert original.status == correction.status == EvidenceStatus.SUPERSEDED
    assert original.status_reason == "Synthetic correction"
    assert latest.status == EvidenceStatus.ACTIVE
    for field in (
        "org_id",
        "subject_employee_id",
        "kind",
        "source",
        "source_ref",
        "visibility",
    ):
        assert getattr(correction, field) == getattr(original, field)
    assert correction.title == "Correction"
    assert correction.occurred_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert correction.event_id is None and correction.provenance == {"correction": True}
    with pytest.raises(DuplicateEvidenceContent):
        correct(session, latest, "original body")
    assert latest.status == EvidenceStatus.ACTIVE
    events = audits(session, original)
    assert len(events) == 2
    event = next(e for e in events if e.target_id == str(original.id))
    assert event.action == "evidence.superseded" and event.target_type == "evidence"
    assert (
        event.actor_type == ActorKind.SYSTEM_WORKER and event.actor_id == "test-worker"
    )
    assert event.details == {
        "original_evidence_id": str(original.id),
        "new_evidence_id": str(correction.id),
        "reason": "Synthetic correction",
        "source": "jira",
        "source_ref": "SYN-1",
    }
    assert "body" not in json.dumps(event.details)


@pytest.mark.parametrize("operation", [correct, retract])
@pytest.mark.parametrize("initial_operation", [correct, retract])
def test_inactive_evidence_rejected(
    evidence: tuple[Session, Evidence],
    operation: Callable[[Session, Evidence], object],
    initial_operation: Callable[[Session, Evidence], object],
) -> None:
    session, original = evidence
    initial_operation(session, original)
    before = original.model_dump()
    with pytest.raises(InactiveEvidence):
        operation(session, original)
    assert original.model_dump() == before
    assert len(audits(session, original)) == 1


def test_retraction_and_retracted_duplicate(evidence: tuple[Session, Evidence]) -> None:
    session, original = evidence
    old = correct(session, original)
    retract(session, old)
    active, _ = append_evidence(
        session,
        org_id=old.org_id,
        subject_employee_id=old.subject_employee_id,
        kind=old.kind,
        source=old.source,
        source_ref=old.source_ref,
        title="New active",
        content={"private": "new active body"},
        visibility=old.visibility,
        provenance={},
    )
    with pytest.raises(DuplicateEvidenceContent):
        correct(session, active)
    assert (
        old.status == EvidenceStatus.RETRACTED
        and old.status_reason == "Synthetic retraction"
    )
    event = next(e for e in audits(session, old) if e.action == "evidence.retracted")
    assert event.target_type == "evidence" and event.target_id == str(old.id)
    assert event.actor_type == ActorKind.USER and event.actor_id == "test-user"
    assert event.details == {
        "original_evidence_id": str(old.id),
        "reason": "Synthetic retraction",
        "source": "jira",
        "source_ref": "SYN-1",
    }


@pytest.mark.parametrize("operation", [correct, retract])
def test_changes_and_audit_rollback(
    evidence: tuple[Session, Evidence], operation: Callable[[Session, Evidence], object]
) -> None:
    session, original = evidence
    with session.begin_nested() as transaction:
        operation(session, original)
        assert len(audits(session, original)) == 1
        transaction.rollback()
    session.refresh(original)
    assert original.status == EvidenceStatus.ACTIVE and original.status_reason is None
    assert audits(session, original) == []
    assert (
        len(
            session.exec(
                select(Evidence).where(Evidence.org_id == original.org_id)
            ).all()
        )
        == 1
    )


@pytest.mark.parametrize(
    "assignment",
    [
        "content = '{}'::jsonb",
        "title = 'Changed'",
        "subject_employee_id = id",
        "content_hash = 'changed'",
        "source_ref = 'Changed'",
        "source = 'Changed'",
        "org_id = id",
        "id = org_id",
        "kind = 'self_report'",
        "event_id = id",
        "visibility = 'standard'",
        "provenance = '{}'::jsonb",
        "occurred_at = now()",
        "created_at = now()",
        "supersedes_evidence_id = id",
    ],
)
def test_immutable_columns(evidence: tuple[Session, Evidence], assignment: str) -> None:
    session, original = evidence
    with pytest.raises(DBAPIError, match="immutable"), session.begin_nested():
        session.execute(
            text(f"UPDATE evidence SET {assignment} WHERE org_id = :org AND id = :id"),
            {"org": original.org_id, "id": original.id},
        )


@pytest.mark.parametrize("status", ["active", "superseded"])
def test_status_cannot_be_restored_or_changed(
    evidence: tuple[Session, Evidence], status: str
) -> None:
    session, original = evidence
    retract(session, original)
    with pytest.raises(DBAPIError, match="status transition"), session.begin_nested():
        session.execute(
            text(
                "UPDATE evidence SET status = :status WHERE org_id = :org AND id = :id"
            ),
            {"status": status, "org": original.org_id, "id": original.id},
        )


def test_delete_requires_transaction_local_switch(
    evidence: tuple[Session, Evidence],
) -> None:
    session, original = evidence
    statement = text("DELETE FROM evidence WHERE org_id = :org AND id = :id")
    params = {"org": original.org_id, "id": original.id}
    with pytest.raises(DBAPIError, match="cannot be deleted"), session.begin_nested():
        session.execute(statement, params)
    with session.begin_nested() as transaction:
        session.execute(text("SET LOCAL app.allow_evidence_delete = 'on'"))
        session.execute(statement, params)
        transaction.rollback()
    with pytest.raises(DBAPIError, match="cannot be deleted"), session.begin_nested():
        session.execute(statement, params)


def test_writers_lock_and_refresh_stale_instances(
    evidence: tuple[Session, Evidence],
) -> None:
    session, original = evidence
    session.commit()
    org_id = original.org_id
    try:
        with Session(engine) as other:
            stale = other.get(Evidence, original.id)
            assert stale is not None
            correct(session, original)
            other.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                retract(other, stale)
            other.rollback()
            session.commit()
        with Session(engine) as other:
            active = session.exec(
                select(Evidence).where(
                    Evidence.org_id == org_id, Evidence.status == EvidenceStatus.ACTIVE
                )
            ).one()
            cached = other.get(Evidence, active.id)
            assert cached is not None and cached.status == EvidenceStatus.ACTIVE
            retract(session, active)
            session.commit()
            with pytest.raises(InactiveEvidence):
                correct(other, cached)
    finally:
        session.rollback()
        delete_test_organization(session, org_id)
