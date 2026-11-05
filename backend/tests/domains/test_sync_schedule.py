from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, select

from app.connectors.jira.client import jira_client_for
from app.core.db import engine
from app.domains.org.models import ExternalIdentity
from app.domains.source import schedule, sync
from app.domains.source.models import SourceConnection, SyncRun
from app.worker.app import app
from app.worker.tasks import dispatch_scheduled
from tests.api.routes.test_sources import BODY
from tests.authz.test_tenant_access import tenant_data  # noqa: F401
from tests.connectors.test_mock_jira import jira  # noqa: F401
from tests.domains.test_sync import context, run_sync, start  # noqa: F401
from tests.utils.migrations import migrate


@pytest.mark.parametrize(
    "anchor,period,expected",
    [
        ("2028-01-31", "monthly", "2028-02-29"),
        ("2026-11-30", "quarterly", "2027-02-28"),
        ("2028-02-29", "yearly", "2029-02-28"),
    ],
)
def test_calendar_period(anchor: str, period: str, expected: str) -> None:
    assert (
        schedule.add_period(datetime.fromisoformat(anchor), period).date().isoformat()
        == expected
    )


def connection_for(db: Session, org: UUID) -> SourceConnection:
    return db.exec(
        select(SourceConnection).where(
            SourceConnection.org_id == org, SourceConnection.kind == "jira"
        )
    ).one()


def test_save_schedule(
    request: pytest.FixtureRequest, client: TestClient, db: Session
) -> None:
    org, base, auth, _, users = request.getfixturevalue("context")
    assert (
        client.get(base + "/sources/jira", headers=auth).json()["sync_schedule"]
        == "off"
    )
    before = datetime.now(UTC)
    response = client.put(
        base + "/sources/jira", headers=auth, json={**BODY, "sync_schedule": "monthly"}
    )
    assert response.status_code == 200
    assert (
        schedule.add_period(before, "monthly")
        <= datetime.fromisoformat(response.json()["next_sync_at"])
        <= schedule.add_period(datetime.now(UTC), "monthly")
    )
    connection = connection_for(db, org)
    db.add(
        SyncRun(
            org_id=org,
            connection_id=connection.id,
            requested_by_user_id=users[0].id,
            status="succeeded",
            finished_at=datetime(2028, 2, 29, 13, tzinfo=UTC),
        )
    )
    db.commit()
    for period, expected in (
        ("quarterly", "2028-05-29T13:00:00Z"),
        ("yearly", "2029-02-28T13:00:00Z"),
        ("off", None),
    ):
        response = client.put(
            base + "/sources/jira", headers=auth, json={**BODY, "sync_schedule": period}
        )
        assert response.status_code == 200
        assert response.json()["next_sync_at"] == expected
    assert (
        client.put(
            base + "/sources/jira",
            headers=auth,
            json={**BODY, "sync_schedule": "daily"},
        ).status_code
        == 422
    )
    dispatch_scheduled(timestamp=0)
    db.expire_all()
    assert not db.exec(
        select(SyncRun).where(SyncRun.org_id == org, SyncRun.trigger == "schedule")
    ).all()
    assert start(client, base, auth)


def make_due(db: Session, org: UUID) -> None:
    connection = connection_for(db, org)
    connection.sync_schedule = "monthly"
    connection.next_sync_at = datetime(2020, 1, 15, tzinfo=UTC)
    db.add(connection)
    db.commit()


def test_concurrent_dispatch_and_missed_periods(
    request: pytest.FixtureRequest, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = request.getfixturevalue("context")[0]
    make_due(db, org)
    now = datetime(2030, 4, 20, tzinfo=UTC)
    queued, release = Event(), Event()
    original = schedule._enqueue

    def enqueue(session: Session, run: SyncRun) -> None:
        original(session, run)
        queued.set()
        assert release.wait(10)

    def first_worker() -> None:
        with Session(engine) as session:
            schedule.dispatch_due_syncs(session, now)

    monkeypatch.setattr(schedule, "_enqueue", enqueue)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(first_worker)
        try:
            assert queued.wait(10)
            with Session(engine) as other:
                schedule.dispatch_due_syncs(other, now)
        finally:
            release.set()
        pending.result(timeout=10)
    db.expire_all()
    runs = db.exec(select(SyncRun).where(SyncRun.org_id == org)).all()
    assert len(runs) == 1 and runs[0].trigger == "schedule"
    assert connection_for(db, org).next_sync_at == datetime(2030, 5, 15, tzinfo=UTC)
    schedule.dispatch_due_syncs(db, now)
    assert db.exec(select(SyncRun).where(SyncRun.org_id == org)).all() == runs
    jobs = (
        db.execute(
            text(
                "SELECT args FROM procrastinate_jobs WHERE task_name = 'sources.sync_run' AND args->>'org_id' = :org"
            ),
            {"org": str(org)},
        )
        .scalars()
        .all()
    )
    assert len(jobs) == 1 and jobs[0]["run_id"] == str(runs[0].id)
    assert (
        app.periodic_registry.periodic_tasks[("sources.dispatch_scheduled", "")].cron
        == "0 * * * *"
    )


@pytest.mark.parametrize("status", ["queued", "running", "failed"])
def test_active_or_failed_run(
    request: pytest.FixtureRequest, client: TestClient, db: Session, status: str
) -> None:
    org, base, auth, *_ = request.getfixturevalue("context")
    run_id = start(client, base, auth)
    run = db.get(SyncRun, UUID(run_id))
    assert run is not None
    run.status = status
    db.add(run)
    db.commit()
    make_due(db, org)
    now = datetime.now(UTC)
    schedule.dispatch_due_syncs(db, now)
    db.expire_all()
    assert connection_for(db, org).next_sync_at > now
    runs = db.exec(select(SyncRun).where(SyncRun.org_id == org)).all()
    assert len(runs) == (2 if status == "failed" else 1)


@pytest.mark.parametrize("deployment", ["cloud", "data_center"])
def test_scheduled_incremental_jql(
    request: pytest.FixtureRequest,
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    deployment: str,
) -> None:
    org, base, auth, *_ = request.getfixturevalue("context")
    if deployment == "data_center":
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
        ).raise_for_status()
    run_sync(db, org, start(client, base, auth))
    water = connection_for(db, org).synced_until
    assert water is not None
    calls: list[Any] = []
    http = request.getfixturevalue("jira")
    http.event_hooks["request"].append(calls.append)
    monkeypatch.setattr(sync, "jira_client_for", lambda c: jira_client_for(c, http))
    make_due(db, org)
    schedule.dispatch_due_syncs(db, datetime.now(UTC))
    run = db.exec(
        select(SyncRun).where(SyncRun.org_id == org, SyncRun.trigger == "schedule")
    ).one()
    result = run_sync(db, org, str(run.id))
    assert result.status == "succeeded" and result.counts["tasks_added"] == 0
    searches = [call for call in calls if "/search" in call.url.path]
    assert searches
    expected = (water - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    assert all(
        expected in str(call.url.params.get("jql", "")) + call.content.decode()
        for call in searches
    )


def test_schedule_migration(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "a84e001bc001")
    assert "sync_schedule" not in {
        c["name"] for c in inspect(migration_engine).get_columns("source_connection")
    }
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM alembic_version")
            ).scalar_one()
            == 1
        )
    migrate(migration_engine, "downgrade", "a84e001bc001")
    migrate(migration_engine, "upgrade", "head")


def test_scheduled_sync_queues_incremental_for_manual_records(
    request: pytest.FixtureRequest, client: TestClient, db: Session
) -> None:
    org, base, auth, employees, _ = request.getfixturevalue("context")
    db.add(
        ExternalIdentity(
            org_id=org,
            source="jira",
            external_id="demo-jira-01",
            employee_id=employees[0].id,
        )
    )
    db.commit()
    run_sync(db, org, start(client, base, auth))
    query = text(
        "SELECT args, lock FROM procrastinate_jobs WHERE task_name='profiles.draft_incremental' AND args->>'org_id'=:org"
    )
    assert not db.execute(query, {"org": str(org)}).all()
    make_due(db, org)
    schedule.dispatch_due_syncs(db, datetime.now(UTC))
    run = db.exec(
        select(SyncRun).where(SyncRun.org_id == org, SyncRun.trigger == "schedule")
    ).one()
    result = run_sync(db, org, str(run.id))
    assert result.status == "succeeded" and result.counts["tasks_added"] == 0
    job = db.execute(query, {"org": str(org)}).one()
    assert str(employees[0].id) in job.args["employee_ids"]
    assert job.lock == f"initial-profiles:{org}"


def test_dispatch_rolls_back_queue_and_due_time(
    request: pytest.FixtureRequest, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = request.getfixturevalue("context")[0]
    make_due(db, org)
    original = schedule._enqueue

    def unavailable(session: Session, run: SyncRun) -> None:
        original(session, run)
        raise RuntimeError("synthetic queue failure")

    monkeypatch.setattr(schedule, "_enqueue", unavailable)
    with pytest.raises(RuntimeError, match="synthetic queue failure"):
        schedule.dispatch_due_syncs(db, datetime.now(UTC))
    db.rollback()
    assert connection_for(db, org).next_sync_at == datetime(2020, 1, 15, tzinfo=UTC)
    assert not db.exec(select(SyncRun).where(SyncRun.org_id == org)).all()
    assert (
        db.execute(
            text(
                "SELECT count(*) FROM procrastinate_jobs WHERE args->>'org_id' = :org"
            ),
            {"org": str(org)},
        ).scalar_one()
        == 0
    )
