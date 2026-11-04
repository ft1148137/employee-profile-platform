import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue
from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import object_session
from sqlmodel import Session, col, delete, select

from app.authz.principal import Principal
from app.core.db import engine
from app.domains.audit.models import AuditEvent
from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.service import activate_template
from app.domains.capability.templates import InvalidTemplate
from app.domains.change_request.models import (
    ChangeItem,
    ProfileChangeRequest,
    RequestStatus,
)
from app.domains.drafting import service
from app.domains.drafting.collect import CollectedRecord
from app.domains.drafting.rules import DraftItem
from app.domains.evidence.service import retract_evidence
from app.domains.org.models import Employee, EmploymentStatus, ExternalIdentity
from app.domains.profile.models import ProfileProgress
from app.worker.app import app
from app.worker.tasks import draft_initial
from tests.authz.test_tenant_access import headers, tenant_data  # noqa: F401
from tests.domains.test_ingest import ingest, person, record, rows
from tests.test_worker import recover_stalled_jobs
from tests.utils.migrations import migrate


def work_queue() -> None:
    async def run() -> None:
        async with app.open_async():
            await asyncio.wait_for(app.run_worker_async(wait=False), timeout=30)

    asyncio.run(run())


def test_stalled_drafting_recovers(
    request: pytest.FixtureRequest, db: Session, client: TestClient
) -> None:
    orgs, _, employees, users = request.getfixturevalue("tenant_data")
    org = orgs[0].id
    url, auth = f"/api/v1/orgs/{org}/initial-profiles", headers(users[0])
    activate_template(db, Principal.system_worker(org), "tech-rnd")
    job_id = draft_initial.configure(
        connection=db.connection().connection.driver_connection,
        lock=f"initial-profiles:{org}",
    ).defer(org_id=str(org))
    db.execute(
        text(
            "UPDATE procrastinate_jobs SET status = 'doing', worker_id = NULL WHERE id = :id"
        ),
        {"id": job_id},
    )
    db.commit()
    assert (
        client.post(url + "/draft", headers=auth, json={"scope": "all"}).status_code
        == 409
    )
    recover_stalled_jobs()
    assert (
        db.execute(
            text("SELECT status FROM procrastinate_jobs WHERE id = :id"), {"id": job_id}
        ).scalar_one()
        == "todo"
    )
    db.rollback()
    work_queue()
    assert (
        client.get(url + "/progress", headers=auth).json()["counts"]["generated"]
        == len(employees) - 1
    )
    assert (
        client.post(url + "/draft", headers=auth, json={"scope": "all"}).status_code
        == 202
    )
    work_queue()


def test_queue_progress_retry(
    request: pytest.FixtureRequest,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orgs, _, employees, users = request.getfixturevalue("tenant_data")
    org = orgs[0].id
    url, auth = f"/api/v1/orgs/{org}/initial-profiles", headers(users[0])
    for i, status in [(1, 403), (2, 403), (3, 404), (4, 404)]:
        assert (
            client.get(url + "/progress", headers=headers(users[i])).status_code
            == status
        )
        assert (
            client.post(
                url + "/draft", headers=headers(users[i]), json={"scope": "all"}
            ).status_code
            == status
        )
    assert (
        client.post(
            url.replace(str(org), str(orgs[1].id)) + "/draft",
            headers=auth,
            json={"scope": "all"},
        ).status_code
        == 404
    )
    assert (
        client.post(url + "/draft", headers=auth, json={"scope": "all"}).status_code
        == 422
    )
    activate_template(db, Principal.system_worker(org), "tech-rnd")
    db.exec(delete(ProfileProgress).where(col(ProfileProgress.org_id) == org))
    employees[4].employment_status = EmploymentStatus.INACTIVE
    db.add(employees[4])
    db.add(
        ExternalIdentity(
            org_id=org, employee_id=employees[1].id, source="jira", external_id="alpha"
        )
    )
    db.flush()
    for index in range(2):
        ingest(db, org, record(person(), key=f"STAT-{index}"))
    db.commit()
    assert (
        client.get(url + "/progress", headers=auth).json()["counts"]["not_started"] == 4
    )
    assert (
        client.post(url + "/draft", headers=auth, json={"scope": "bad"}).status_code
        == 422
    )
    assert client.post(
        url + "/draft", headers=auth, json={"scope": "failed"}
    ).json() == {"queued": 0}
    original = service.write_initial_request
    failed_employee = employees[1].id
    failure: Exception = RuntimeError("Synthetic sensitive exception")
    original_draft = service.rules.draft

    def update_source(
        records: list[CollectedRecord],
        capabilities: list[CapabilityDefinition],
        keywords: dict[str, list[str]],
    ) -> list[DraftItem]:
        items = original_draft(records, capabilities, keywords)
        if items and len(records) > 1:
            session = object_session(records[0].evidence)
            assert isinstance(session, Session)
            retract_evidence(
                session,
                evidence=records[0].evidence,
                reason="来源在起草过程中更正",
                actor_type=Principal.system_worker(org).actor_type,
                actor_id=None,
            )
        return items

    monkeypatch.setattr(service.rules, "draft", update_source)

    def fail_one(
        session: Session,
        org_id: UUID,
        employee: Employee,
        items: list[DraftItem],
        generated_by: dict[str, JsonValue],
    ) -> bool:
        result = original(session, org_id, employee, items, generated_by)
        if employee.id == failed_employee:
            raise failure
        return result

    monkeypatch.setattr(service, "write_initial_request", fail_one)
    assert client.post(url + "/draft", headers=auth, json={"scope": "all"}).json() == {
        "queued": 4
    }
    assert (
        client.post(url + "/draft", headers=auth, json={"scope": "all"}).status_code
        == 409
    )
    work_queue()
    result = client.get(url + "/progress", headers=auth).json()
    assert (
        result["mode"] == "rules"
        and result["counts"]["failed"] == 1
        and result["counts"]["generated"] == 3
    )
    assert len(result["failed"]) == 1 and "起草失败" in result["failed"][0]["error"]
    assert "Synthetic sensitive exception" not in str(result)
    db.expire_all()
    requests = rows(db, ProfileChangeRequest, org)
    assert len(requests) == 3 and not rows(db, ChangeItem, org)
    assert all(r.subject_employee_id != failed_employee for r in requests)
    assert all(r.generated_by["keywords_ref"].startswith("tech-rnd@") for r in requests)
    monkeypatch.setattr(service, "write_initial_request", original)
    assert client.post(
        url + "/draft", headers=auth, json={"scope": "failed"}
    ).json() == {"queued": 1}
    work_queue()
    result = client.get(url + "/progress", headers=auth).json()
    assert (
        result["counts"]["generated"] == 4
        and result["requests"]["pending_owner"] == 4
        and not result["failed"]
    )
    items = rows(db, ChangeItem, org)
    assert len(items) == 1 and items[0].after is not None
    assert items[0].after["evidence_count"] == 1
    assert items[0].after["statement"].startswith("有 1 项")
    for failure in (
        service.InvalidDraft("起草结果使用了词典以外的等级"),
        InvalidTemplate("关键词文件格式无效"),
    ):
        monkeypatch.setattr(service, "write_initial_request", fail_one)
        draft_initial(org_id=str(org), employee_ids=[str(failed_employee)])
        assert client.get(url + "/progress", headers=auth).json()["failed"][0][
            "error"
        ] == str(failure)
    monkeypatch.setattr(service, "write_initial_request", original)
    draft_initial(org_id=str(org), employee_ids=[str(failed_employee)])
    db.expire_all()
    handed = rows(db, ProfileChangeRequest, org)[0]
    handed.status = RequestStatus.PENDING_REVIEW
    db.add(handed)
    db.commit()
    draft_initial(org_id=str(org))
    result = client.get(url + "/progress", headers=auth).json()
    assert result["requests"] == {"pending_owner": 3, "handed_over": 1}
    audit = db.exec(
        select(AuditEvent)
        .where(
            AuditEvent.org_id == org, AuditEvent.action == "initial_profiles.drafted"
        )
        .order_by(col(AuditEvent.created_at).desc())
    ).first()
    assert audit and audit.details == {
        "mode": "rules",
        "generated": 3,
        "failed": 0,
        "handed_over": 1,
    }
    with Session(engine) as session:
        assert (
            session.execute(
                text(
                    "SELECT count(*) FROM procrastinate_jobs WHERE task_name='profiles.draft_initial' AND status='failed'"
                )
            ).scalar_one()
            == 0
        )


def test_enqueue_rollback(
    request: pytest.FixtureRequest,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    org = orgs[0].id
    activate_template(db, Principal.system_worker(org), "tech-rnd")
    db.exec(delete(ProfileProgress).where(col(ProfileProgress.org_id) == org))
    db.commit()

    def fail(**_kwargs: object) -> None:
        raise RuntimeError("Synthetic queue unavailable")

    monkeypatch.setattr(draft_initial, "configure", fail)
    with pytest.raises(RuntimeError, match="Synthetic queue"):
        client.post(
            f"/api/v1/orgs/{org}/initial-profiles/draft",
            headers=headers(users[0]),
            json={"scope": "all"},
        )
    assert not rows(db, ProfileProgress, org)


def test_migration(migration_engine: Engine) -> None:
    assert "inference_error" in {
        c["name"] for c in inspect(migration_engine).get_columns("profile_progress")
    }
    migrate(migration_engine, "downgrade", "093e2d94e5aa")
    assert "inference_error" not in {
        c["name"] for c in inspect(migration_engine).get_columns("profile_progress")
    }
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        assert (
            len(
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).all()
            )
            == 1
        )
