import asyncio
import hashlib
import json
from collections.abc import Generator
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid7

import pytest
from fastapi.testclient import TestClient
from procrastinate.exceptions import JobRetry
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.connectors.jira.client import RetryableSourceError, jira_client_for
from app.connectors.jira.mock import create_mock_app
from app.core.db import engine
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.evidence.models import Evidence
from app.domains.org.models import ExternalIdentity, Organization
from app.domains.source import sync
from app.domains.source.models import SourceConnection, SyncRun
from app.domains.work.models import Task, WorkEvent
from app.worker.app import app
from app.worker.tasks import sync_run
from tests.api.routes.test_sources import BODY
from tests.authz.test_tenant_access import headers, tenant_data  # noqa: F401
from tests.connectors.test_mock_jira import EXAMPLES, jira  # noqa: F401
from tests.test_worker import recover_stalled_jobs


@pytest.fixture
def context(
    request: pytest.FixtureRequest,
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple]:
    orgs, _, employees, users = request.getfixturevalue("tenant_data")
    http = request.getfixturevalue("jira")
    monkeypatch.setattr(sync, "jira_client_for", lambda c: jira_client_for(c, http))
    base, auth = f"/api/v1/orgs/{orgs[0].id}", headers(users[0])
    assert client.post(base + "/sources/jira/sync", headers=auth).status_code == 422
    assert (
        client.put(
            base + "/sources/jira",
            headers=auth,
            json={**BODY, "start_date": "2000-01-01"},
        ).status_code
        == 200
    )
    yield orgs[0].id, base, auth, employees, users
    db.rollback()
    db.execute(
        text(
            "DELETE FROM procrastinate_jobs WHERE task_name IN ('sources.sync_run', 'profiles.draft_incremental') AND args->>'org_id' = :org"
        ),
        {"org": str(orgs[0].id)},
    )
    db.commit()


def run_sync(db: Session, org: UUID, run_id: str, **kwargs: Any) -> SyncRun:
    sync.execute_sync(
        Principal.system_worker(org), UUID(run_id), page_size=10, **kwargs
    )
    db.expire_all()
    return db.exec(
        select(SyncRun).where(SyncRun.org_id == org, SyncRun.id == UUID(run_id))
    ).one()


def start(client: TestClient, base: str, auth: dict[str, str]) -> str:
    response = client.post(base + "/sources/jira/sync", headers=auth)
    assert response.status_code == 202, response.text
    return response.json()["id"]


@pytest.mark.parametrize("deployment", ["cloud", "data_center"])
def test_stalled_sync_resumes_committed_page(
    context: tuple,
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    deployment: str,
) -> None:
    org, base, auth, employees, _ = context
    if deployment == "data_center":
        assert (
            client.put(
                base + "/sources/jira",
                headers=auth,
                json={
                    **BODY,
                    "deployment": deployment,
                    "auth_type": "pat",
                    "secret": "mock-dc-pat",
                    "start_date": "2000-01-01",
                },
            ).status_code
            == 200
        )
    db.add(
        ExternalIdentity(
            org_id=org,
            source="jira",
            external_id=(
                "demo-jira-01"
                if deployment == "cloud"
                else "JIRAUSER"
                + str(int(hashlib.sha256(b"demo-jira-01").hexdigest()[:12], 16))
            ),
            employee_id=employees[0].id,
        )
    )
    db.commit()
    run_id = start(client, base, auth)
    original = sync._batch

    class WorkerKilled(BaseException):
        pass

    def stop_after_commit(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        raise WorkerKilled

    monkeypatch.setattr(sync, "_batch", stop_after_commit)
    with pytest.raises(WorkerKilled):
        run_sync(db, org, run_id)
    db.expire_all()
    run = db.get(SyncRun, UUID(run_id))
    assert run.status == "running" and run.counts["records"] == 10
    assert run.cursor["next_cursor"]
    db.execute(
        text(
            "UPDATE procrastinate_jobs SET status = 'doing', worker_id = NULL "
            "WHERE task_name = 'sources.sync_run' AND args->>'run_id' = :run"
        ),
        {"run": run_id},
    )
    db.commit()
    assert client.post(base + "/sources/jira/sync", headers=auth).status_code == 409
    monkeypatch.setattr(sync, "_batch", original)
    recover_stalled_jobs()

    async def work() -> None:
        async with app.open_async():
            await asyncio.wait_for(app.run_worker_async(wait=False), timeout=20)

    asyncio.run(work())
    db.expire_all()
    assert run.status == "succeeded" and run.counts["records"] == 37
    assert run.counts["tasks_added"] == 37
    sizes = [
        len(db.exec(select(m).where(m.org_id == org)).all())
        for m in (Task, WorkEvent, Evidence)
    ]
    assert sizes[0] == 37 and sizes[2] > 0
    repeated = run_sync(db, org, start(client, base, auth))
    assert repeated.status == "succeeded"
    assert sizes == [
        len(db.exec(select(m).where(m.org_id == org)).all())
        for m in (Task, WorkEvent, Evidence)
    ]


def test_jira_resume_incremental_and_migration(
    context: tuple,
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    org, base, auth, employees, _ = context
    db.add(
        ExternalIdentity(
            org_id=org,
            source="jira",
            external_id="demo-jira-01",
            employee_id=employees[0].id,
        )
    )
    db.commit()
    run_id = start(client, base, auth)
    assert client.post(base + "/sources/jira/sync", headers=auth).status_code == 409
    original, calls = sync.ingest_record, 0

    def ingest(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 12:
            raise ValueError("private-token")
        return original(*args, **kwargs)

    monkeypatch.setattr(sync, "ingest_record", ingest)
    run = run_sync(db, org, run_id)
    assert (
        run.status == "failed"
        and run.counts["records"] == 10
        and run.cursor["next_cursor"]
    )
    assert "private-token" not in run.error
    connection = db.exec(
        select(SourceConnection).where(SourceConnection.org_id == org)
    ).one()
    assert connection.synced_until is None
    monkeypatch.setattr(sync, "ingest_record", original)
    assert (
        client.post(base + f"/sync-runs/{run_id}/retry", headers=auth).status_code
        == 202
    )
    run = run_sync(db, org, run_id, initial_attempts=1)
    assert (
        run.status == "succeeded"
        and run.attempts == 2
        and run.counts["tasks_added"] == 37
    )
    sizes = [
        len(db.exec(select(m).where(m.org_id == org)).all())
        for m in (Task, WorkEvent, Evidence)
    ]
    assert sizes[0] == 37 and sizes[1] and sizes[2]
    assert db.exec(
        select(ExternalIdentity).where(
            ExternalIdentity.org_id == org, col(ExternalIdentity.employee_id).is_(None)
        )
    ).first()
    keys = {t.external_key for t in db.exec(select(Task).where(Task.org_id == org))}
    connection = db.exec(
        select(SourceConnection).where(SourceConnection.org_id == org)
    ).one()
    water = connection.synced_until
    # Force full replay, then verify the normal incremental overlap as well.
    connection.synced_until = None
    db.add(connection)
    db.commit()
    for _ in range(2):
        run = run_sync(db, org, start(client, base, auth))
        assert run.status == "succeeded"
        assert all(
            value == 0
            for key, value in run.counts.items()
            if key.endswith(("added", "updated", "superseded", "retracted"))
        )
        assert [
            len(db.exec(select(m).where(m.org_id == org)).all())
            for m in (Task, WorkEvent, Evidence)
        ] == sizes
    path = tmp_path / "examples/demo-org/jira/issues.json"
    data = json.loads(path.read_text())
    data["issues"][0]["fields"].update(
        status={"name": "Changed"}, updated="2030-01-01T00:00:00+00:00"
    )
    new = deepcopy(data["issues"][0])
    new["key"] = "SYNTHETIC-999"
    data["issues"].append(new)
    path.write_text(json.dumps(data))
    http = TestClient(create_mock_app(tmp_path / "examples"))
    monkeypatch.setattr(sync, "jira_client_for", lambda c: jira_client_for(c, http))
    run = run_sync(db, org, start(client, base, auth))
    assert (
        run.counts["tasks_added"]
        == run.counts["tasks_updated"]
        == run.counts["evidence_superseded"]
        == 1
    )
    db.refresh(connection)
    assert connection.synced_until > water
    assert (
        client.put(
            base + "/sources/jira",
            headers=auth,
            json={
                **BODY,
                "deployment": "data_center",
                "auth_type": "pat",
                "secret": "mock-dc-pat",
                "start_date": "2000-01-01",
            },
        ).status_code
        == 200
    )
    db.refresh(connection)
    assert connection.synced_until is None
    run = run_sync(db, org, start(client, base, auth))
    assert (
        run.counts["records"] == 38
        and run.counts["tasks_added"] == run.counts["evidence_added"] == 0
    )
    assert keys | {"SYNTHETIC-999"} == {
        t.external_key for t in db.exec(select(Task).where(Task.org_id == org))
    }
    listing = client.get(base + "/sync-runs?limit=1&skip=1", headers=auth).json()
    assert listing["count"] == 5 and len(listing["data"]) == 1
    assert not {"input", "cursor", "secret_ciphertext"} & listing["data"][0].keys()


def test_csv_api_and_worker(context: tuple, client: TestClient, db: Session) -> None:
    org, base, auth, _, users = context
    url = base + "/sources/csv/imports"
    before = len(db.exec(select(SyncRun).where(SyncRun.org_id == org)).all())
    bad = client.post(url, headers=auth, files={"file": ("bad.csv", b"title\nx")})
    assert (
        bad.status_code == 422
        and bad.json()["detail"]["code"] == "work_records_invalid"
    )
    assert (
        client.post(
            url, headers=auth, files={"file": ("big.csv", b"x" * (5 * 1024 * 1024 + 1))}
        ).status_code
        == 413
    )
    assert len(db.exec(select(SyncRun).where(SyncRun.org_id == org)).all()) == before
    content = (EXAMPLES / "test-enterprises/qiguang-aiot/work_records.csv").read_bytes()
    for index in range(2):
        response = client.post(url, headers=auth, files={"file": ("work.csv", content)})
        assert response.status_code == 202
        assert (
            client.post(
                url, headers=auth, files={"file": ("work.csv", content)}
            ).status_code
            == 409
        )
        run_id = response.json()["run"]["id"]
        if index == 0:

            async def work() -> None:
                async with app.open_async():
                    await asyncio.wait_for(
                        app.run_worker_async(wait=False, concurrency=1), timeout=20
                    )

            asyncio.run(work())
            db.expire_all()
            run = db.get(SyncRun, UUID(run_id))
        else:
            run = run_sync(db, org, run_id)
        assert run.status == "succeeded" and run.counts["records"] == 32
        assert run.counts["tasks_added"] == (32 if index == 0 else 0)
    jira_connection = db.exec(
        select(SourceConnection).where(
            SourceConnection.org_id == org, SourceConnection.kind == "jira"
        )
    ).one()
    old_jira = SyncRun(
        org_id=org,
        connection_id=jira_connection.id,
        requested_by_user_id=users[0].id,
        status="succeeded",
        created_at=utc_now() - timedelta(days=2),
    )
    db.add(old_jira)
    db.commit()
    for kind, expected_id, count in (("jira", str(old_jira.id), 1), ("csv", run_id, 2)):
        listing = client.get(
            base + f"/sync-runs?kind={kind}&limit=1", headers=auth
        ).json()
        assert listing["count"] == count and listing["data"][0]["id"] == expected_id
    assert client.get(base + "/sync-runs?kind=unknown", headers=auth).status_code == 422
    for fmt, mime in (
        ("csv", "text/csv"),
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ):
        response = client.get(
            base + f"/sources/csv/template?format={fmt}", headers=auth
        )
        assert response.status_code == 200 and response.headers[
            "content-type"
        ].startswith(mime)
        assert "filename*=UTF-8''%E5%B7%A5" in response.headers["content-disposition"]
    assert (
        client.get(base + "/sources/csv/template?format=txt", headers=auth).status_code
        == 422
    )
    for index, status in ((1, 403), (2, 403), (3, 404)):
        for method, path in (
            ("POST", "/sources/jira/sync"),
            ("POST", "/sources/csv/imports"),
            ("GET", "/sources/csv/template"),
            ("GET", "/sync-runs"),
            ("POST", f"/sync-runs/{run_id}/retry"),
        ):
            assert (
                client.request(
                    method,
                    base + path,
                    headers=headers(users[index]),
                    files={"file": ("work.csv", content)}
                    if path.endswith("imports")
                    else None,
                ).status_code
                == status
            )


def test_retry_limits_scope_and_stalled(
    context: tuple, client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, base, auth, _, _ = context
    run_id = start(client, base, auth)
    run = db.get(SyncRun, UUID(run_id))
    # A valid foreign tenant must not consume or mutate this run.
    other = db.exec(select(Organization).where(Organization.id != org)).first()
    sync_run(org_id=str(other.id), run_id=run_id)
    db.refresh(run)
    assert run.status == "queued" and run.attempts == 0
    assert (
        client.post(base + f"/sync-runs/{run_id}/retry", headers=auth).status_code
        == 409
    )
    assert (
        client.post(base + f"/sync-runs/{uuid7()}/retry", headers=auth).status_code
        == 404
    )

    def unavailable(_: Any) -> Any:
        raise RetryableSourceError("Jira 暂时不可用", retry_after=0)

    monkeypatch.setattr(sync, "jira_client_for", unavailable)
    for attempt in range(1, 4):
        with pytest.raises(JobRetry):
            run_sync(db, org, run_id)
        db.refresh(run)
        assert run.status == "queued" and run.attempts == attempt
    run = run_sync(db, org, run_id)
    assert run.status == "failed" and run.attempts == 4
    assert (
        client.post(base + f"/sync-runs/{run_id}/retry", headers=auth).status_code
        == 202
    )
    run = run_sync(
        db, org, run_id
    )  # exhausted unless a new queue job supplies its baseline
    run.status, run.updated_at = "running", utc_now() - timedelta(minutes=31)
    db.add(run)
    db.commit()
    assert (
        client.post(base + f"/sync-runs/{run_id}/retry", headers=auth).status_code
        == 202
    )
    audit = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org, AuditEvent.action == "sync_run.failed"
        )
    ).all()
    assert audit and all(set(a.details) == set(run.counts) for a in audit)


def test_csv_merge_roles(context: tuple, client: TestClient, db: Session) -> None:
    org, base, auth, _, _ = context
    content = b"record_id,source,occurred_at,actor_account,title,participants\n1,test,2026-01-01,actor,test,a:reviewer;a:recorder;b\n"
    response = client.post(
        base + "/sources/csv/imports",
        headers=auth,
        files={"file": ("work.csv", content)},
    )
    assert response.status_code == 202 and response.json()["issues"]
    run = run_sync(db, org, response.json()["run"]["id"])
    assert run.status == "succeeded"
    task = db.exec(select(Task).where(Task.org_id == org)).one()
    assert [p["role"] for p in task.attributes["people"]] == [
        "执行人",
        "reviewer、recorder",
        "参与人",
    ]


@pytest.mark.parametrize("deployment", ["cloud", "data_center"])
@pytest.mark.parametrize("retry_after", [0, 1])
def test_real_queue_retry(
    context: tuple,
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    deployment: str,
    retry_after: int,
) -> None:
    org, base, auth, _, _ = context
    if deployment == "data_center":
        assert (
            client.put(
                base + "/sources/jira",
                headers=auth,
                json={
                    **BODY,
                    "deployment": deployment,
                    "auth_type": "pat",
                    "secret": "mock-dc-pat",
                    "start_date": "2000-01-01",
                },
            ).status_code
            == 200
        )
    original, calls = sync.jira_client_for, 0

    def connect(connection: SourceConnection) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableSourceError("Jira 暂时不可用", retry_after=retry_after)
        return original(connection)

    monkeypatch.setattr(sync, "jira_client_for", connect)
    run_id = start(client, base, auth)

    async def work() -> None:
        # Scheduled retries must not depend on the app and DB clocks matching.
        async with app.open_async(), asyncio.TaskGroup() as tasks:
            worker = tasks.create_task(
                app.run_worker_async(
                    wait=True,
                    concurrency=1,
                    fetch_job_polling_interval=0.05,
                    install_signal_handlers=False,
                )
            )
            async with asyncio.timeout(20):
                while True:
                    with Session(engine) as progress:
                        status = progress.exec(
                            select(SyncRun.status).where(
                                SyncRun.org_id == org, SyncRun.id == UUID(run_id)
                            )
                        ).one()
                    if status in {"succeeded", "failed"}:
                        break
                    await asyncio.sleep(0.05)
            worker.cancel()

    asyncio.run(work())
    db.expire_all()
    run = db.get(SyncRun, UUID(run_id))
    assert (
        run.status == "succeeded"
        and run.attempts == 2
        and run.counts["tasks_added"] == 37
    )


def test_lock_and_active_retry(context: tuple, client: TestClient, db: Session) -> None:
    org, base, auth, _, _ = context
    run_id = start(client, base, auth)
    run = db.get(SyncRun, UUID(run_id))
    run.status, run.updated_at = "running", utc_now() - timedelta(minutes=31)
    db.commit()
    with engine.connect() as connection:
        key = {"key": str(run.connection_id)}
        connection.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), key
        )
        try:
            with pytest.raises(JobRetry):
                run_sync(db, org, run_id)
            assert run.status == "running" and run.attempts == 0
            assert (
                client.post(
                    base + f"/sync-runs/{run_id}/retry", headers=auth
                ).status_code
                == 409
            )
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), key
            )
            connection.commit()
    run.status = "failed"
    db.add(run)
    db.commit()
    another = start(client, base, auth)
    assert (
        client.post(base + f"/sync-runs/{run_id}/retry", headers=auth).status_code
        == 409
    )
    assert run_sync(db, org, another).status == "succeeded"


def test_import_serializes_account_linking(
    context: tuple, client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, base, auth, employees, _ = context
    identity = ExternalIdentity(org_id=org, source="csv:test", external_id="actor")
    db.add(identity)
    db.commit()
    identity_id = identity.id
    original = sync.ingest_record

    def ingest(*args: Any, **kwargs: Any) -> Any:
        with Session(engine) as contender:
            with pytest.raises(OperationalError) as caught:
                contender.exec(
                    select(ExternalIdentity)
                    .where(
                        ExternalIdentity.org_id == org,
                        ExternalIdentity.id == identity_id,
                    )
                    .with_for_update(nowait=True)
                ).one()
            assert caught.value.orig.sqlstate == "55P03"
        return original(*args, **kwargs)

    monkeypatch.setattr(sync, "ingest_record", ingest)
    content = b"record_id,source,occurred_at,actor_account,title\n1,test,2026-01-01,actor,test\n"
    response = client.post(
        base + "/sources/csv/imports",
        headers=auth,
        files={"file": ("work.csv", content)},
    )
    assert run_sync(db, org, response.json()["run"]["id"]).status == "succeeded"
    linked = client.post(
        base + "/external-identities/link",
        headers=auth,
        json={
            "links": [
                {"identity_id": str(identity_id), "employee_id": str(employees[0].id)}
            ],
        },
    )
    assert linked.status_code == 200 and linked.json()["counts"]["evidence_added"] == 1
