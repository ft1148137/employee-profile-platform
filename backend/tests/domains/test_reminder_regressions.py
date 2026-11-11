from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.core.db import engine
from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition
from app.domains.drafting.models import UpdateSetting
from app.domains.drafting.update_settings import months_before
from app.domains.evidence.models import EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence, retract_evidence
from app.domains.inbox import reminders
from app.domains.org.models import Employee, Organization, OrgMembership
from app.domains.profile.models import (
    AssessmentStatus,
    ClaimEvidence,
    EvidenceRelation,
    ProfileClaim,
)
from tests.domains.test_update_reminders import questions


@pytest.fixture
def reminder_claim(demo: tuple, db: Session) -> tuple[Employee, ProfileClaim]:
    _, people, _ = demo
    owner = people["E02"]
    capability = db.exec(
        select(CapabilityDefinition).where(CapabilityDefinition.org_id == owner.org_id)
    ).first()
    employee = Employee(org_id=owner.org_id, display_name="Synthetic reminder employee")
    db.add(employee)
    db.flush()
    claim = ProfileClaim(
        org_id=owner.org_id,
        subject_employee_id=employee.id,
        capability_definition_id=capability.id,
        statement="Synthetic reminder claim",
        assessment_status=AssessmentStatus.CONFIRMED,
        generated_by={"actor_type": "user"},
    )
    db.add(claim)
    db.commit()
    return employee, claim


@pytest.mark.parametrize(
    "case,expected",
    [
        ("self_report", True),
        ("unknown", False),
        ("empty", False),
        ("retracted", False),
        ("same_source", True),
        ("two_sources", False),
    ],
)
def test_single_source(
    reminder_claim: tuple[Employee, ProfileClaim],
    db: Session,
    case: str,
    expected: bool,
) -> None:
    employee, claim = reminder_claim
    now = utc_now()
    if case == "unknown":
        claim.assessment_status = AssessmentStatus.UNKNOWN
        db.add(claim)
    sources = [] if case == "empty" else ["synthetic"]
    if case in ("same_source", "two_sources"):
        sources.append("synthetic" if case == "same_source" else "independent")
    for index, source in enumerate(sources):
        evidence, _ = append_evidence(
            db,
            org_id=employee.org_id,
            subject_employee_id=employee.id,
            kind=EvidenceKind.WORK_RECORD
            if len(sources) == 2
            else EvidenceKind.SELF_REPORT,
            source=source,
            source_ref=f"REGRESSION-{index}",
            title="Synthetic evidence",
            content={"text": "synthetic"},
            visibility=Visibility.RESTRICTED,
            provenance={},
            occurred_at=now,
        )
        if case == "retracted":
            retract_evidence(
                db,
                evidence=evidence,
                reason="Synthetic withdrawal",
                actor_type="user",
                actor_id=None,
            )
        db.add(
            ClaimEvidence(
                org_id=employee.org_id,
                claim_id=claim.id,
                evidence_id=evidence.id,
                relation=EvidenceRelation.SUPPORTS,
            )
        )
    db.commit()
    reminders.generate_questions(db, employee, now, 24, months_before(now, 3))
    db.commit()
    assert any(q.kind == "single_source" for q in questions(db, employee)) == expected


def test_all_capabilities_and_record_title(
    reminder_claim: tuple[Employee, ProfileClaim],
    demo: tuple,
    db: Session,
    client: TestClient,
) -> None:
    employee, claim = reminder_claim
    base, _, auth = demo
    now = utc_now()
    for ref, linked in [("LINKED-1", True), ("UNLINKED-1", False)]:
        evidence, _ = append_evidence(
            db,
            org_id=employee.org_id,
            subject_employee_id=employee.id,
            kind=EvidenceKind.WORK_RECORD,
            source="synthetic",
            source_ref=ref,
            title="合成项目迁移",
            content={"text": "synthetic"},
            visibility=Visibility.RESTRICTED,
            provenance={},
            occurred_at=months_before(now, 30),
        )
        if linked:
            db.add(
                ClaimEvidence(
                    org_id=employee.org_id,
                    claim_id=claim.id,
                    evidence_id=evidence.id,
                    relation=EvidenceRelation.CONTRADICTS,
                )
            )
    db.commit()
    assert (
        client.put(
            base + "/drafting-settings",
            headers=auth["admin"],
            json={"enabled": False},
        ).status_code
        == 200
    )
    reminders.generate_questions(db, employee, now, 24, months_before(now, 3))
    db.commit()
    rows = questions(db, employee)
    assert [q.kind for q in rows] == ["conflict", "stale"]
    reminders.generate_questions(db, employee, now, 24, months_before(now, 3))
    db.commit()
    rows = questions(db, employee)
    assert [q.kind for q in rows] == ["conflict", "stale", "single_source", "record"]
    assert rows[3].question == "「合成项目迁移」（UNLINKED-1）里你负责的是哪部分？"


def test_evidence_loaded_once(
    reminder_claim: tuple[Employee, ProfileClaim], db: Session
) -> None:
    employee, claim = reminder_claim
    db.refresh(claim)
    for _ in range(5):
        db.add(ProfileClaim(**claim.model_dump(exclude={"id"})))
    db.commit()
    statements: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        now = utc_now()
        reminders.generate_questions(db, employee, now, 24, months_before(now, 3))
        db.commit()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert sum("FROM claim_evidence JOIN evidence" in s for s in statements) == 1
    assert not any("FROM task" in s for s in statements)


def test_generation_releases_tenant_lock(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, people, _ = demo
    org_id = people["E02"].org_id
    now = utc_now()
    db.add(UpdateSetting(org_id=org_id, next_reminder_at=now - timedelta(seconds=1)))
    db.commit()
    original = reminders.generate_questions
    checked: list[Employee] = []

    def generate(
        session: Session, employee: Employee, at: datetime, months: int, since: datetime
    ) -> None:
        if employee.org_id == org_id:
            with Session(engine) as other:
                assert other.get(UpdateSetting, org_id).next_reminder_at > now
                # Approval takes a shared tenant lock; settings saves take an exclusive one.
                for shared in (True, False):
                    other.exec(
                        select(Organization)
                        .where(Organization.id == org_id)
                        .with_for_update(read=shared, nowait=True)
                    ).one()
                    other.rollback()
            checked.append(employee)
        original(session, employee, at, months, since)
        with Session(engine) as other:
            other.exec(
                select(Organization)
                .where(Organization.id == employee.org_id)
                .with_for_update(read=True, nowait=True)
            ).one()

    monkeypatch.setattr(reminders, "generate_questions", generate)
    reminders.dispatch_due_reminders(db, now)
    assert checked and all(questions(db, e) for e in checked)


class SyntheticReminderFailure(Exception):
    pass


def test_failure_isolated_and_previous_employee_committed(
    demo: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, people, _ = demo
    org_id = people["E02"].org_id
    now = utc_now()
    foreign = Organization(slug="reminder-regression", name="Synthetic tenant")
    db.add(foreign)
    db.flush()
    employee = Employee(org_id=foreign.id, display_name="Synthetic foreign employee")
    db.add(employee)
    db.flush()
    user_id = db.exec(
        select(OrgMembership.user_id).where(
            OrgMembership.org_id == org_id,
            OrgMembership.employee_id == people["E02"].id,
        )
    ).one()
    db.add(OrgMembership(org_id=foreign.id, employee_id=employee.id, user_id=user_id))
    for tenant in (org_id, foreign.id):
        db.add(
            UpdateSetting(org_id=tenant, next_reminder_at=now - timedelta(seconds=1))
        )
    db.commit()
    original = reminders.generate_questions
    attempted: list[Employee] = []

    def generate(
        session: Session, target: Employee, at: datetime, months: int, since: datetime
    ) -> None:
        original(session, target, at, months, since)
        if target.org_id == org_id:
            attempted.append(target)
            if len(attempted) == 2:
                raise SyntheticReminderFailure("sensitive exception content")

    monkeypatch.setattr(reminders, "generate_questions", generate)
    try:
        reminders.dispatch_due_reminders(db, now)
        assert len(attempted) == 2
        assert questions(db, attempted[0])
        assert not questions(db, attempted[1])
        assert questions(db, employee)
        assert "SyntheticReminderFailure" in caplog.text
        assert "sensitive exception content" not in caplog.text
    finally:
        from tests.utils.org import delete_test_organization

        delete_test_organization(db, foreign.id)
