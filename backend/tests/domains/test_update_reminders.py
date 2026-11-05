from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect
from sqlmodel import Session, col, select

from app.core.db import engine
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.drafting.models import UpdateSetting
from app.domains.drafting.update_settings import months_before
from app.domains.evidence.models import EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence
from app.domains.inbox.models import InboxItem, PendingQuestion, QuestionStatus
from app.domains.inbox.reminders import dispatch_due_reminders, generate_questions
from app.domains.org.models import (
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
)
from app.domains.profile.models import (
    AssessmentStatus,
    ClaimEvidence,
    EvidenceRelation,
    ProfileClaim,
)
from app.domains.source.schedule import add_period
from app.models import User
from app.worker.app import app
from app.worker.tasks import dispatch_reminders
from tests.api.routes.test_drafting_settings import snapshot
from tests.utils.migrations import migrate


def questions(db: Session, employee: Employee) -> list[PendingQuestion]:
    return list(
        db.exec(
            select(PendingQuestion)
            .where(
                PendingQuestion.org_id == employee.org_id,
                PendingQuestion.target_employee_id == employee.id,
                PendingQuestion.reason == "定期画像确认",
            )
            .order_by(col(PendingQuestion.created_at), col(PendingQuestion.id))
        ).all()
    )


def test_settings_permissions_and_audit(
    demo: tuple, client: TestClient, db: Session
) -> None:
    base, people, auth = demo
    url = f"{base}/update-settings"
    initial = client.get(url, headers=auth["admin"]).json()
    org_id = people["E02"].org_id
    assert (
        initial["reminder_schedule"] == "quarterly"
        and initial["stale_after_months"] == 24
    )
    assert initial["updated_at"] is None
    for role, status in (("employee", 403), ("manager", 403), ("foreign", 404)):
        assert client.get(url, headers=auth[role]).status_code == status
        assert client.put(url, headers=auth[role], json={}).status_code == status
    for body in (
        {"stale_after_months": 5},
        {"stale_after_months": 121},
        {"reminder_schedule": "monthly"},
        {"org_id": str(org_id)},
    ):
        assert client.put(url, headers=auth["admin"], json=body).status_code == 422
    for schedule in ("yearly", "off", "quarterly"):
        before = utc_now()
        body = {"reminder_schedule": schedule, "stale_after_months": 36}
        response = client.put(url, headers=auth["admin"], json=body)
        assert response.status_code == 200
        data = response.json()
        assert data["updated_at"]
        if schedule == "off":
            assert data["next_reminder_at"] is None
        else:
            assert (
                add_period(before, schedule)
                <= datetime.fromisoformat(data["next_reminder_at"])
                <= add_period(utc_now(), schedule)
            )
        assert client.put(url, headers=auth["admin"], json=body).json() == data
    audits = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org_id, AuditEvent.action == "update_settings.updated"
        )
    ).all()
    assert (
        len(audits) == 3 and audits[0].details["after"]["reminder_schedule"] == "yearly"
    )


@pytest.mark.parametrize("schedule", ["quarterly", "yearly", "off", "default"])
def test_due_once_and_downtime(demo: tuple, db: Session, schedule: str) -> None:
    _, people, _ = demo
    employee = people["E02"]
    now = utc_now()
    dispatch_due_reminders(db, now)
    assert not questions(db, employee)
    org = db.get(Organization, employee.org_id)
    assert org is not None
    if schedule == "default":
        org.created_at = months_before(now, 40)
        db.add(org)
    else:
        db.add(
            UpdateSetting(
                org_id=employee.org_id,
                reminder_schedule=schedule,
                next_reminder_at=months_before(now, 40),
            )
        )
    db.commit()
    before = snapshot(db, org.id)
    with ThreadPoolExecutor(max_workers=2) as pool:

        def run(_: int) -> None:
            with Session(engine) as session:
                dispatch_due_reminders(session, now)

        list(pool.map(run, range(2)))
    db.expire_all()
    first = questions(db, employee)
    assert len(first) == (0 if schedule == "off" else 2)
    dispatch_due_reminders(db, now)
    assert [q.id for q in questions(db, employee)] == [q.id for q in first]
    assert snapshot(db, org.id) == before
    if schedule != "off":
        setting = db.get(UpdateSetting, org.id)
        assert setting.next_reminder_at > now
        # Even after a later period, existing open questions are never duplicated.
        setting.next_reminder_at = now
        db.add(setting)
        db.commit()
        dispatch_due_reminders(db, now)
        pairs = [(q.kind, q.capability_definition_id) for q in questions(db, employee)]
        assert len(set(pairs)) == len(pairs)
    assert (
        app.periodic_registry.periodic_tasks[("profiles.dispatch_reminders", "")].cron
        == "0 * * * *"
    )


def test_priority_dedup_and_answer(
    demo: tuple, client: TestClient, db: Session
) -> None:
    base, people, auth = demo
    employee, now = people["E02"], utc_now()
    claim = db.exec(
        select(ProfileClaim).where(
            ProfileClaim.org_id == employee.org_id,
            ProfileClaim.subject_employee_id == employee.id,
            ProfileClaim.assessment_status == AssessmentStatus.CONFIRMED,
        )
    ).first()
    assert claim
    # Replace the current claim with a synthetic confirmed claim whose only evidence is old and conflicting.
    current = ProfileClaim(
        org_id=employee.org_id,
        subject_employee_id=employee.id,
        capability_definition_id=claim.capability_definition_id,
        statement="Synthetic current claim",
        assessment_status=AssessmentStatus.CONFIRMED,
        generated_by={"actor_type": "user"},
        supersedes_claim_id=claim.id,
    )
    db.add(current)
    db.flush()
    evidence, _ = append_evidence(
        db,
        org_id=employee.org_id,
        subject_employee_id=employee.id,
        kind=EvidenceKind.WORK_RECORD,
        source="synthetic",
        source_ref="EDGE-231",
        title="Synthetic record",
        content={"text": "synthetic"},
        visibility=Visibility.STANDARD,
        provenance={},
        occurred_at=months_before(now, 30),
    )
    db.add(
        ClaimEvidence(
            org_id=employee.org_id,
            claim_id=current.id,
            evidence_id=evidence.id,
            relation=EvidenceRelation.CONTRADICTS,
        )
    )
    db.commit()
    for existing in db.exec(
        select(PendingQuestion).where(
            PendingQuestion.org_id == employee.org_id,
            PendingQuestion.target_employee_id == employee.id,
        )
    ).all():
        existing.status = QuestionStatus.DISMISSED
        db.add(existing)
    db.commit()
    before = snapshot(db, employee.org_id)
    for _ in range(4):
        generate_questions(db, employee, now, 24, months_before(now, 3))
        db.commit()
    rows = questions(db, employee)
    assert [q.kind for q in rows[:2]] == ["conflict", "stale"]
    assert rows[0].claim_id == current.id
    assert "现在还在用吗" in rows[1].question
    assert "single_source" in [q.kind for q in rows[2:4]]
    assert "record" in [q.kind for q in rows] and "general" in [q.kind for q in rows]
    assert len({(q.kind, q.capability_definition_id) for q in rows}) == len(rows)
    assert snapshot(db, employee.org_id) == before
    inbox = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == employee.org_id,
            col(InboxItem.question_id).in_([q.id for q in rows]),
        )
    ).all()
    assert len(inbox) == len(rows) and all(i.kind == "question" for i in inbox)
    path = f"{base}/me/questions/{rows[0].id}/answer"
    for role in ("manager", "admin", "foreign"):
        assert (
            client.post(path, headers=auth[role], json={"answer": "越权"}).status_code
            == 404
        )
    response = client.post(
        path, headers=auth["employee"], json={"answer": "目前仍在使用"}
    )
    assert response.status_code == 200 and response.json()["status"] == "answered"
    assert client.get(
        f"{base}/evidence/{response.json()['answer_evidence_id']}",
        headers=auth["admin"],
    ).json()["redacted"]
    assert (
        db.get(ProfileClaim, current.id).assessment_status == AssessmentStatus.CONFIRMED
    )


@pytest.mark.parametrize(
    "excluded", ["opt_out", "inactive", "no_account", "disabled_account", "department"]
)
def test_employee_eligibility(demo: tuple, db: Session, excluded: str) -> None:
    _, people, _ = demo
    employee = people["E02"]
    if excluded == "opt_out":
        employee.auto_draft_opt_out = True
    elif excluded == "inactive":
        employee.employment_status = EmploymentStatus.INACTIVE
    else:
        membership = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == employee.org_id,
                OrgMembership.employee_id == employee.id,
            )
        ).one()
        if excluded == "no_account":
            membership.employee_id = None
            db.add(membership)
        elif excluded == "disabled_account":
            account = db.get(User, membership.user_id)
            account.is_active = False
            db.add(account)
        else:
            from app.domains.org.models import Department

            department = db.get(Department, employee.department_id)
            department.auto_draft_disabled = True
            db.add(department)
    db.add(employee)
    db.add(
        UpdateSetting(
            org_id=employee.org_id, next_reminder_at=utc_now() - timedelta(seconds=1)
        )
    )
    db.commit()
    try:
        dispatch_reminders(timestamp=0)
        assert not questions(db, employee)
    finally:
        if excluded == "disabled_account":
            account.is_active = True
            db.add(account)
            db.commit()


def test_stale_flag_changes_immediately(
    demo: tuple, client: TestClient, db: Session
) -> None:
    base, people, auth = demo
    employee = people["E02"]
    url = f"{base}/employees/{employee.id}/profile"
    original = client.get(url, headers=auth["employee"]).json()
    before = snapshot(db, employee.org_id)
    # Demo evidence is from 2026: at 2030 only confirmed claims should be stale.
    from unittest.mock import patch

    with patch(
        "app.domains.profile.service.utc_now",
        return_value=datetime(2030, 11, 1, tzinfo=UTC),
    ):
        old = client.get(url, headers=auth["employee"]).json()
        assert any(i["evidence_stale"] for i in old["items"])
        assert all(
            not i["evidence_stale"] for i in old["items"] if i["status"] != "confirmed"
        )
        assert (
            client.put(
                f"{base}/update-settings",
                headers=auth["admin"],
                json={"stale_after_months": 120},
            ).status_code
            == 200
        )
        fresh = client.get(url, headers=auth["employee"]).json()
        assert not any(i["evidence_stale"] for i in fresh["items"])
        assert [i["status"] for i in fresh["items"]] == [
            i["status"] for i in original["items"]
        ]
    assert snapshot(db, employee.org_id) == before
    assert months_before(datetime(2028, 8, 31, tzinfo=UTC), 6).day == 29


def test_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "a98e001bc001")
    assert "update_setting" not in inspect(migration_engine).get_table_names()
    assert "kind" not in {
        c["name"] for c in inspect(migration_engine).get_columns("pending_question")
    }
    migrate(migration_engine, "upgrade", "head")
    assert "update_setting" in inspect(migration_engine).get_table_names()
    assert {"kind", "claim_id"} <= {
        c["name"] for c in inspect(migration_engine).get_columns("pending_question")
    }
