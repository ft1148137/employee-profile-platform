from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid7

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, inspect
from sqlmodel import Session, select

from app.core.db import engine
from app.domains.audit.models import ActorKind, AuditEvent
from app.domains.evidence.models import Evidence, EvidenceStatus
from app.domains.org.models import (
    Employee,
    EmploymentStatus,
    ExternalIdentity,
    Organization,
)
from app.domains.source.linking import auto_link_identities
from app.domains.source.models import SourceConnection, SyncRun
from app.domains.source.sync import _batch
from app.domains.work.ingest import (
    SourcePerson,
    SourceRecord,
    ingest_record,
    prepare_identities,
)
from tests.authz.test_tenant_access import headers
from tests.authz.test_tenant_access import tenant_data as tenant_fixture
from tests.utils.migrations import migrate

tenant_data = tenant_fixture


def record(key: str, **person: Any) -> SourceRecord:
    return SourceRecord(
        source="jira",
        key=key,
        title="Synthetic task",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        people=[
            SourcePerson(
                external_id="synthetic-account",
                role="assignee",
                event_ref="assignee",
                **person,
            )
        ],
    )


def test_page_links_backfills_and_respects_unlink(
    tenant_data: tuple,
    db: Session,
    client: TestClient,
) -> None:
    orgs, _, employees, users = tenant_data
    org, employee = orgs[0], employees[0]
    employee.work_email = "\t Match@Example.com\n\u3000"
    employees[-1].work_email = "match@example.com"
    connection = SourceConnection(
        org_id=org.id, kind="jira", updated_by_user_id=users[0].id
    )
    db.add_all([employee, employees[-1], connection])
    db.flush()
    run = SyncRun(
        org_id=org.id,
        connection_id=connection.id,
        requested_by_user_id=users[0].id,
        counts={"records": 0},
    )
    db.add(run)
    db.commit()
    _batch(db, run, [record("OLD")], {})
    assert not db.exec(select(Evidence).where(Evidence.org_id == org.id)).all()
    _batch(db, run, [record("NEW", email="  MATCH@example.COM  ")], {})
    identity = db.exec(
        select(ExternalIdentity).where(ExternalIdentity.org_id == org.id)
    ).one()
    assert identity.employee_id == employee.id
    assert identity.linked_by == "system_worker:email_match" and identity.linked_at
    evidence = db.exec(select(Evidence).where(Evidence.org_id == org.id)).all()
    assert {e.source_ref for e in evidence} == {"OLD", "NEW"}
    assert {e.subject_employee_id for e in evidence} == {employee.id}
    assert run.counts["evidence_added"] == 2
    audit = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org.id, AuditEvent.action == "external_identity.linked"
        )
    ).one()
    assert audit.actor_type == ActorKind.SYSTEM_WORKER and audit.actor_id is None
    assert audit.details["reason"] == "email_match"
    assert "match@example" not in str(audit.details).lower()
    url, auth = f"/api/v1/orgs/{org.id}/external-identities", headers(users[0])
    row = client.get(url, headers=auth, params={"status": "linked"}).json()["data"][0]
    assert row["linked_by"] == "system_worker:email_match"
    assert client.post(url + f"/{identity.id}/unlink", headers=auth).status_code == 200
    db.refresh(identity)
    assert identity.auto_link_blocked
    _batch(db, run, [record("NEW", email="match@example.com")], {})
    db.refresh(identity)
    assert identity.employee_id is None
    assert all(
        e.status == EvidenceStatus.RETRACTED
        for e in db.exec(select(Evidence).where(Evidence.org_id == org.id))
    )
    response = client.post(
        url + "/link",
        headers=auth,
        json={
            "links": [
                {"identity_id": str(identity.id), "employee_id": str(employee.id)}
            ]
        },
    )
    assert (
        response.status_code == 200 and response.json()["counts"]["evidence_added"] == 2
    )
    db.refresh(identity)
    assert identity.employee_id == employee.id
    # Repeated pages are idempotent after a manual override.
    _batch(db, run, [record("NEW", email="match@example.com")], {})
    assert (
        len(
            db.exec(
                select(AuditEvent).where(
                    AuditEvent.org_id == org.id,
                    AuditEvent.action == "external_identity.linked",
                )
            ).all()
        )
        == 2
    )


def test_only_unique_active_local_emails_link(tenant_data: tuple, db: Session) -> None:
    orgs, _, employees, _ = tenant_data
    org = orgs[0]
    employees[0].work_email = "duplicate@example.com"
    employees[1].work_email = " DUPLICATE@example.COM "
    employees[2].work_email = "inactive@example.com"
    employees[2].employment_status = EmploymentStatus.INACTIVE
    employees[-1].work_email = "foreign@example.com"
    db.add_all(employees)
    db.commit()
    for index, email in enumerate(
        (
            "duplicate@example.com",
            "inactive@example.com",
            "foreign@example.com",
            None,
            "   ",
        )
    ):
        ingest_record(
            db,
            org_id=org.id,
            record=record(
                str(index), email=email, display_name=employees[0].display_name
            ),
            provenance={},
        )
    identity = db.exec(
        select(ExternalIdentity).where(ExternalIdentity.org_id == org.id)
    ).one()
    assert identity.employee_id is None
    assert not db.exec(select(Evidence).where(Evidence.org_id == org.id)).all()
    # An inactive duplicate does not make the one active employee ambiguous.
    employees[1].employment_status = EmploymentStatus.INACTIVE
    db.add(employees[1])
    ingest_record(
        db,
        org_id=org.id,
        record=record("unique", email="duplicate@example.com"),
        provenance={},
    )
    assert identity.employee_id == employees[0].id
    foreign = ExternalIdentity(
        org_id=orgs[1].id,
        source="jira",
        external_id="foreign",
        email="duplicate@example.com",
    )
    db.add(foreign)
    assert auto_link_identities(db, org.id, [foreign]) == []
    assert foreign.employee_id is None


def test_page_matching_has_constant_query_count(
    tenant_data: tuple, db: Session
) -> None:
    orgs, _, _, _ = tenant_data
    org_id = orgs[0].id
    totals = []
    statements: list[str] = []

    def capture(*args: Any) -> None:
        statements.append(args[2])

    for size in (1, 50):
        people = []
        for index in range(size):
            email = f"batch-{size}-{index}@example.com"
            db.add(Employee(org_id=org_id, display_name="Synthetic", work_email=email))
            people.append(
                SourcePerson(
                    external_id=email,
                    email=email,
                    role="assignee",
                    event_ref=str(index),
                )
            )
        db.commit()
        page = SourceRecord(
            source="jira",
            key=f"BATCH-{size}",
            title="Synthetic",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            people=people,
        )
        statements.clear()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            prepare_identities(db, org_id, [page])
            db.flush()
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        totals.append(len(statements))
        assert sum("FROM employee" in statement for statement in statements) == 1
        db.commit()
    assert totals[0] == totals[1]


def test_auto_link_rolls_back_with_page(
    tenant_data: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    orgs, _, employees, _ = tenant_data
    org_id: UUID = orgs[0].id
    employees[0].work_email = "rollback@example.com"
    db.add(employees[0])
    db.commit()
    from app.domains.work import ingest

    class PageFailed(Exception):
        pass

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise PageFailed

    monkeypatch.setattr(ingest, "_derive", fail)
    with pytest.raises(PageFailed):
        ingest_record(
            db,
            org_id=org_id,
            record=record("ROLLBACK", email="rollback@example.com"),
            provenance={},
        )
    db.rollback()
    assert not db.exec(
        select(ExternalIdentity).where(ExternalIdentity.org_id == org_id)
    ).all()
    assert not db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org_id, AuditEvent.action == "external_identity.linked"
        )
    ).all()


def test_auto_link_marker_migration(migration_engine: Engine) -> None:
    orgs = [
        Organization(slug=str(uuid7()), name="Synthetic migration") for _ in range(2)
    ]
    identities = [
        ExternalIdentity(org_id=orgs[0].id, source="jira", external_id=str(i))
        for i in range(3)
    ]
    identity_ids = [identity.id for identity in identities]
    with Session(migration_engine) as session:
        session.add_all(orgs)
        session.flush()
        session.add_all(identities)
        # The foreign audit must not block the local account with the same target ID.
        for org, identity in ((orgs[0], identities[1]), (orgs[1], identities[2])):
            session.add(
                AuditEvent(
                    org_id=org.id,
                    actor_type=ActorKind.USER,
                    actor_id=str(uuid7()),
                    action="external_identity.unlinked",
                    target_type="external_identity",
                    target_id=str(identity.id),
                    details={},
                )
            )
        session.commit()
    migrate(migration_engine, "downgrade", "a88e001bc001")
    assert "auto_link_blocked" not in {
        c["name"] for c in inspect(migration_engine).get_columns("external_identity")
    }
    migrate(migration_engine, "upgrade", "head")
    column = next(
        c
        for c in inspect(migration_engine).get_columns("external_identity")
        if c["name"] == "auto_link_blocked"
    )
    assert column["nullable"] is False and column["default"] == "false"
    with Session(migration_engine) as session:
        flags = dict(
            session.exec(
                select(ExternalIdentity.id, ExternalIdentity.auto_link_blocked)
            ).all()
        )
    assert [flags[identity_id] for identity_id in identity_ids] == [False, True, False]
