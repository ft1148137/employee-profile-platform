from collections.abc import Generator
from datetime import timedelta
from uuid import UUID, uuid7

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlmodel import Session, SQLModel, select

from app.core.db import engine
from app.domains.audit.models import ActorKind, AuditEvent
from app.domains.base import utc_now
from app.domains.evidence.models import Evidence, EvidenceStatus
from app.domains.evidence.service import retract_evidence
from app.domains.org.models import Employee, ExternalIdentity, Organization
from app.domains.work.ingest import (
    IdentityNotFound,
    IngestCounts,
    SourcePerson,
    SourceRecord,
    ingest_record,
    rederive_identity,
)
from app.domains.work.models import Task, WorkEvent
from tests.utils.migrations import migrate

type Context = tuple[Session, UUID, list[ExternalIdentity]]


@pytest.fixture
def context() -> Generator[Context]:
    with Session(engine) as session:
        org = Organization(slug=str(uuid7()), name="Synthetic ingest org")
        session.add(org)
        session.flush()
        identities = []
        for account in ("alpha", "beta"):
            employee = Employee(
                org_id=org.id, display_name="Same name", work_email="same@example.com"
            )
            session.add(employee)
            session.flush()
            identity = ExternalIdentity(
                org_id=org.id,
                employee_id=employee.id,
                source="jira",
                external_id=account,
                linked_at=utc_now(),
            )
            session.add(identity)
            identities.append(identity)
        session.flush()
        yield session, org.id, identities
        session.rollback()


def person(account: str = "alpha", role: str = "assignee") -> SourcePerson:
    return SourcePerson(
        external_id=account,
        display_name="Same name",
        email="same@example.com",
        role=role,
        event_ref=role,
    )


def record(*people: SourcePerson, **updates: object) -> SourceRecord:
    return SourceRecord.model_validate(
        {
            "source": "jira",
            "key": "SYN-1",
            "title": "Synthetic task",
            "status": "open",
            "created_at": "2026-01-01T09:00:00+08:00",
            "updated_at": "2026-01-02T09:00:00+08:00",
            "occurred_at": "2026-01-01T09:00:00+08:00",
            "people": list(people),
            "labels": ["python"],
            "components": ["api"],
            "detail": "Synthetic detail",
            **updates,
        }
    )


def rows[T: SQLModel](session: Session, model: type[T], org_id: UUID) -> list[T]:
    return list(session.exec(select(model).where(model.org_id == org_id)))


def ingest(session: Session, org_id: UUID, source: SourceRecord) -> IngestCounts:
    return ingest_record(
        session, org_id=org_id, record=source, provenance={"sync_run_id": "synthetic"}
    )


def test_roles_idempotency_and_metadata(context: Context) -> None:
    session, org, identities = context
    source = record(person(), person("beta", "reporter"))
    assert ingest(session, org, source) == IngestCounts(
        tasks_added=1, events_added=2, evidence_added=2
    )
    before = {
        m.__name__: [r.model_dump() for r in rows(session, m, org)]
        for m in (Task, WorkEvent, Evidence, ExternalIdentity)
    }
    assert ingest(session, org, source) == IngestCounts(evidence_unchanged=2)
    assert before == {
        m.__name__: [r.model_dump() for r in rows(session, m, org)]
        for m in (Task, WorkEvent, Evidence, ExternalIdentity)
    }
    evidence = rows(session, Evidence, org)
    assert {e.content["roles"][0] for e in evidence} == {"assignee", "reporter"}
    assert (
        len(
            {
                tuple((k, str(v)) for k, v in e.content.items() if k != "roles")
                for e in evidence
            }
        )
        == 1
    )
    assert all(e.provenance == {"sync_run_id": "synthetic"} for e in evidence)
    assert all(e.content["created"] == "2026-01-01T01:00:00Z" for e in evidence)
    assert not {"people", "email", "external_id", "name"} & evidence[0].content.keys()
    assert all(
        e.event_id in {w.id for w in rows(session, WorkEvent, org)} for e in evidence
    )
    source.people = [person(), person("alpha", "reporter")]
    result = ingest(session, org, source)
    assert (
        result.evidence_superseded
        == result.evidence_retracted
        == result.events_updated
        == 1
    )
    active = next(
        e for e in rows(session, Evidence, org) if e.status == EvidenceStatus.ACTIVE
    )
    assert active.subject_employee_id == identities[0].employee_id
    assert active.content["roles"] == ["assignee", "reporter"]


def test_two_accounts_one_employee(context: Context) -> None:
    session, org, identities = context
    identities[1].employee_id = identities[0].employee_id
    result = ingest(session, org, record(person(), person("beta", "reporter")))
    assert result.events_added == 2 and result.evidence_added == 1
    assert rows(session, Evidence, org)[0].content["roles"] == ["assignee", "reporter"]


@pytest.mark.parametrize("source", ["jira", "csv:PLM"])
def test_corrections_and_rollback(context: Context, source: str) -> None:
    session, org, identities = context
    identities[0].source = source
    original = record(
        person(),
        source=source,
        updated_at=None if source.startswith("csv:") else utc_now(),
    )
    ingest(session, org, original)
    old = rows(session, Evidence, org)[0]
    changed = record(
        person(), source=source, status="done", updated_at=original.updated_at
    )
    with session.begin_nested() as transaction:
        assert ingest(session, org, changed).evidence_superseded == 1
        audit = rows(session, AuditEvent, org)[0]
        assert (
            audit.action == "evidence.superseded"
            and audit.actor_type == ActorKind.SYSTEM_WORKER
        )
        transaction.rollback()
    session.refresh(old)
    assert old.status == EvidenceStatus.ACTIVE and rows(session, AuditEvent, org) == []
    assert ingest(session, org, changed).evidence_superseded == 1
    assert ingest(session, org, original).evidence_superseded == 1
    assert len(rows(session, Evidence, org)) == 3
    assert old.status == EvidenceStatus.SUPERSEDED
    assert ingest(session, org, original) == IngestCounts(evidence_unchanged=1)
    original.updated_at = utc_now() + timedelta(days=1)
    assert ingest(session, org, original) == IngestCounts(
        tasks_updated=1, evidence_unchanged=1
    )


def test_reassignment_and_restoration(context: Context) -> None:
    session, org, identities = context
    ingest(session, org, record(person()))
    original = rows(session, Evidence, org)[0]
    event_id = rows(session, WorkEvent, org)[0].id
    result = ingest(session, org, record(person("beta")))
    assert result == IngestCounts(
        tasks_updated=1, events_updated=1, evidence_added=1, evidence_retracted=1
    )
    assert original.status == EvidenceStatus.RETRACTED
    assert (
        rows(session, WorkEvent, org)[0].actor_employee_id == identities[1].employee_id
    )
    result = ingest(session, org, record(person()))
    assert (
        result.evidence_added == result.evidence_retracted == result.events_updated == 1
    )
    active = next(
        e for e in rows(session, Evidence, org) if e.status == EvidenceStatus.ACTIVE
    )
    assert active.content["restored_from"] == str(original.id)
    assert rows(session, WorkEvent, org)[0].id == event_id
    assert ingest(session, org, record(person())) == IngestCounts(evidence_unchanged=1)
    assert all(a.action == "evidence.retracted" for a in rows(session, AuditEvent, org))


def test_unlinked_and_cloud_migration(context: Context) -> None:
    session, org, identities = context
    ingest(session, org, record(person(), person("beta", "reporter")))
    evidence_ids = {e.id for e in rows(session, Evidence, org)}
    result = ingest(
        session, org, record(person("cloud-alpha"), person("cloud-beta", "reporter"))
    )
    assert result == IngestCounts(tasks_updated=1, people_unlinked=2)
    assert len(rows(session, Task, org)) == 1
    assert {e.id for e in rows(session, Evidence, org)} == evidence_ids
    for account, employee in zip(
        ("cloud-alpha", "cloud-beta"), identities, strict=True
    ):
        identity = next(
            i for i in rows(session, ExternalIdentity, org) if i.external_id == account
        )
        assert identity.employee_id is None and identity.linked_at is None
        assert identity.email == "same@example.com"
        identity.employee_id = employee.employee_id
        identity.linked_at = utc_now()
    assert rederive_identity(session, org_id=org, identity=identity) == IngestCounts(
        evidence_unchanged=2
    )
    assert {e.id for e in rows(session, Evidence, org)} == evidence_ids
    assert all(e.status == EvidenceStatus.ACTIVE for e in rows(session, Evidence, org))


def test_link_and_repeated_restore(context: Context) -> None:
    session, org, linked = context
    source = record(
        person("new-account"), source="csv:PLM", created_at=None, updated_at=None
    )
    assert ingest(session, org, source) == IngestCounts(
        tasks_added=1, people_unlinked=1
    )
    assert rows(session, WorkEvent, org) == rows(session, Evidence, org) == []
    identity = next(
        i
        for i in rows(session, ExternalIdentity, org)
        if i.external_id == "new-account"
    )
    identity.employee_id = linked[0].employee_id
    identity.linked_at = utc_now()
    assert rederive_identity(session, org_id=org, identity=identity) == IngestCounts(
        events_added=1, evidence_added=1
    )
    for _ in range(2):
        old = next(
            e for e in rows(session, Evidence, org) if e.status == EvidenceStatus.ACTIVE
        )
        retract_evidence(
            session,
            evidence=old,
            reason="Synthetic unlink",
            actor_type=ActorKind.SYSTEM_WORKER,
            actor_id=None,
        )
        assert rederive_identity(
            session, org_id=org, identity=identity
        ) == IngestCounts(evidence_added=1)
        active = next(
            e for e in rows(session, Evidence, org) if e.status == EvidenceStatus.ACTIVE
        )
        assert active.content["restored_from"] == str(old.id)
        assert old.status == EvidenceStatus.RETRACTED
        assert rederive_identity(
            session, org_id=org, identity=identity
        ) == IngestCounts(evidence_unchanged=1)
    assert (
        ingest(
            session, org, record(source="csv:PLM", created_at=None, updated_at=None)
        ).evidence_retracted
        == 1
    )
    assert ingest(session, org, source).evidence_added == 1
    assert ingest(session, org, source) == IngestCounts(evidence_unchanged=1)
    assert rows(session, Evidence, org)[-1].occurred_at == source.occurred_at


def test_metadata_changes_do_not_relink(context: Context) -> None:
    session, org, identities = context
    ingest(session, org, record(person()))
    identity = identities[0]
    linked_at = identity.linked_at
    updated = person()
    updated.display_name, updated.email = "New name", "new@example.com"
    assert ingest(session, org, record(updated)) == IngestCounts(
        tasks_updated=1, evidence_unchanged=1
    )
    assert identity.display_name == "New name" and identity.email == "new@example.com"
    assert identity.employee_id is not None and identity.linked_at == linked_at


def test_tenant_and_source_isolation(context: Context) -> None:
    session, org, identities = context
    ingest(session, org, record(person()))
    other = Organization(slug=str(uuid7()), name="Synthetic other tenant")
    session.add(other)
    session.flush()
    assert ingest(session, other.id, record(person())) == IngestCounts(
        tasks_added=1, people_unlinked=1
    )
    with pytest.raises(IdentityNotFound):
        rederive_identity(session, org_id=other.id, identity=identities[0])
    assert ingest(session, org, record(person(), source="csv:PLM")) == IngestCounts(
        tasks_added=1, people_unlinked=1
    )
    assert (
        len(rows(session, Evidence, org)) == 1
        and rows(session, Evidence, other.id) == []
    )
    assert rederive_identity(
        session, org_id=org, identity=identities[0]
    ) == IngestCounts(evidence_unchanged=1)


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "git"},
        {"source": "csv:"},
        {"key": ""},
        {"occurred_at": "2026-01-01"},
        {"people": [person(), person()]},
    ],
)
def test_invalid_input(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        record(**changes)


def test_external_identity_migration(migration_engine: Engine) -> None:
    with migration_engine.begin() as connection:
        org_id, employee_id = uuid7(), uuid7()
        connection.execute(
            text(
                "INSERT INTO organization (id, slug, name, created_at) VALUES (:id, :slug, 'Synthetic migration', now())"
            ),
            {"id": org_id, "slug": str(org_id)},
        )
        connection.execute(
            text(
                "INSERT INTO employee (id, org_id, display_name, employment_status, created_at, updated_at) VALUES (:id, :org, 'Synthetic', 'active', now(), now())"
            ),
            {"id": employee_id, "org": org_id},
        )
        for employee in (None, employee_id):
            connection.execute(
                text(
                    "INSERT INTO external_identity (id, org_id, employee_id, source, external_id, email) VALUES (:id, :org, :employee, 'jira', :external, 'synthetic@example.com')"
                ),
                {
                    "id": uuid7(),
                    "org": org_id,
                    "employee": employee,
                    "external": str(employee),
                },
            )
    migrate(migration_engine, "downgrade", "f58a901bc001-1")
    with migration_engine.connect() as connection:
        result = connection.execute(
            text(
                "SELECT employee_id, linked_at FROM external_identity WHERE org_id = :org"
            ),
            {"org": org_id},
        ).all()
        assert (
            len(result) == 1
            and result[0].employee_id == employee_id
            and result[0].linked_at is not None
        )
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT email FROM external_identity WHERE org_id = :org"),
                {"org": org_id},
            ).scalar_one()
            is None
        )
