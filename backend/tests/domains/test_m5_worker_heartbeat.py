import asyncio
import subprocess
import sys
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from procrastinate import PsycopgConnector, periodic
from sqlalchemy import Engine, MetaData, delete, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.api import deps
from app.authz.principal import Principal
from app.core import db as core_db
from app.core.db import engine
from app.core.security import create_access_token
from app.domains.admin import overview
from app.domains.base import utc_now
from app.domains.capability.service import activate_template
from app.domains.drafting import incremental, model, service
from app.domains.drafting.models import DraftingSetting
from app.domains.evidence.models import EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence
from app.domains.im import gateway, notifications
from app.domains.im.models import ImBinding, ImNotification, ImSetting
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.service import add_item
from app.domains.model.models import ModelSetting
from app.domains.org.models import (
    Employee,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.source import sync
from app.domains.source.models import SourceConnection, SyncRun
from app.models import User
from app.worker import health, monitor, tasks
from app.worker.app import app
from app.worker.models import WorkerHeartbeat
from app.worker.monitor import dispatch_worker_alerts
from tests.utils.migrations import migrate
from tests.utils.org import delete_test_organization


@pytest.fixture
def db(migration_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Generator[Session]:
    # Global monitoring must not scan or write the shared demo used by other tests.
    monkeypatch.setattr(core_db, "engine", migration_engine)
    monkeypatch.setattr(deps, "engine", migration_engine)
    monkeypatch.setattr(tasks, "engine", migration_engine)
    monkeypatch.setattr(
        monitor, "PROCESS_STARTED_AT", utc_now() - health.HEARTBEAT_TIMEOUT
    )
    monkeypatch.setattr(sys.modules[__name__], "engine", migration_engine)
    monkeypatch.setenv(
        "DATABASE_URL", migration_engine.url.render_as_string(hide_password=False)
    )
    with Session(migration_engine) as session:
        yield session


@pytest.fixture
def tenants(db: Session) -> Generator[list[list[OrgMembership]]]:
    result = []
    users = []
    for _ in range(2):
        org = Organization(slug=f"heartbeat-{uuid4()}", name="合成心跳测试企业")
        db.add(org)
        db.flush()
        members = []
        for index in range(3):
            user = User(
                username=f"heartbeat-{uuid4()}@example.com", hashed_password="synthetic"
            )
            db.add(user)
            employee = Employee(org_id=org.id, display_name="合成测试员工")
            db.add(employee)
            db.flush()
            member = OrgMembership(
                org_id=org.id,
                user_id=user.id,
                employee_id=employee.id if index != 1 else None,
            )
            db.add(member)
            db.flush()
            if index != 2:
                db.add(
                    RoleGrant(
                        org_id=org.id, membership_id=member.id, role=Role.ORG_ADMIN
                    )
                )
            if index != 1:
                db.add(
                    ImBinding(
                        org_id=org.id,
                        employee_id=employee.id,
                        platform="feishu",
                        external_user_id=str(user.id),
                        external_chat_id=str(user.id),
                        bot_identity="synthetic-bot",
                    )
                )
            members.append(member)
            users.append(user)
        db.add(
            ImSetting(
                org_id=org.id,
                platform="feishu",
                enabled=True,
                credentials="synthetic-encrypted-value",
                bot_identity="synthetic-bot",
                updated_by_user_id=members[0].user_id,
            )
        )
        result.append(members)
    db.commit()
    yield result
    db.rollback()
    for members in result:
        delete_test_organization(db, members[0].org_id)
    for user in users:
        db.delete(user)
    db.commit()


def test_heartbeat_task_updates_singleton(db: Session) -> None:
    periodic = app.periodic_registry.periodic_tasks[("system.heartbeat", "")]
    assert periodic.cron == "* * * * *"
    before = utc_now()
    tasks.heartbeat(timestamp=0)
    first = db.get(WorkerHeartbeat, 1)
    assert first and before <= first.beat_at <= utc_now()
    first_at = first.beat_at
    tasks.heartbeat(timestamp=0)
    db.expire_all()
    rows = db.exec(select(WorkerHeartbeat)).all()
    assert len(rows) == 1 and rows[0].beat_at >= first_at


@pytest.mark.parametrize("kind", ["initial", "incremental", "jira"])
def test_long_task_progress_keeps_worker_healthy(
    db: Session,
    tenants: list,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    member = tenants[0][0]
    principal = Principal.system_worker(member.org_id)
    now = utc_now()
    started_at = now
    monkeypatch.setattr(health, "utc_now", lambda: now)
    for module in (service, incremental, sync):
        monkeypatch.setattr(module, "engine", engine)
    health.write_heartbeat(engine)
    calls = 0

    def progress(*_args: object, **_kwargs: object) -> model.DraftResult:
        nonlocal now, calls
        now += timedelta(minutes=2)
        calls += 1
        assert health.main() == 0
        dispatch_worker_alerts(db, now)
        assert not db.exec(select(InboxItem)).all()
        return model.DraftResult([], {})

    if kind == "jira":
        connection = SourceConnection(
            org_id=member.org_id,
            updated_by_user_id=member.user_id,
            kind="jira",
            config={
                "deployment": "cloud",
                "base_url": "https://synthetic.example.com",
                "auth_type": "api_token",
                "username": "synthetic@example.com",
            },
        )
        db.add(connection)
        db.flush()
        run = SyncRun(
            org_id=member.org_id,
            connection_id=connection.id,
            requested_by_user_id=member.user_id,
        )
        db.add(run)
        db.commit()

        def page(*_args: object) -> SimpleNamespace:
            progress()
            return SimpleNamespace(
                issues=[], next_cursor=str(calls) if calls < 3 else None
            )

        jira = MagicMock()
        jira.__enter__.return_value.search.side_effect = page
        monkeypatch.setattr(sync, "jira_client_for", lambda _: jira)
        sync._ingest(db, run, connection, 100)
    else:
        activate_template(db, principal, "tech-rnd")
        db.add(
            DraftingSetting(
                org_id=member.org_id,
                auto_draft_enabled=True,
                updated_by_user_id=member.user_id,
            )
        )
        db.add(
            ModelSetting(
                org_id=member.org_id,
                enabled=True,
                kind="openai_compatible",
                provider="openai-compatible",
                base_url="https://synthetic.example.com/v1",
                model="synthetic",
                updated_by_user_id=member.user_id,
            )
        )
        if kind == "initial":
            for employee in service.active_employees(db, member.org_id):
                append_evidence(
                    db,
                    org_id=member.org_id,
                    subject_employee_id=employee.id,
                    kind=EvidenceKind.WORK_RECORD,
                    source="csv:synthetic",
                    source_ref=str(employee.id),
                    title="合成心跳测试工作记录",
                    content={},
                    visibility=Visibility.STANDARD,
                    provenance={},
                    occurred_at=started_at,
                )
        db.commit()
        monkeypatch.setattr(service, "validate_capabilities", lambda *_: None)
        monkeypatch.setattr(model, "draft", progress)
        monkeypatch.setattr(incremental, "draft_employee", progress)
        if kind == "initial":
            service.run_drafting(principal, None)
        else:
            incremental.run_incremental(principal)
    assert calls == 3 and now - started_at > health.HEARTBEAT_TIMEOUT
    assert health.main() == 0
    dispatch_worker_alerts(db, now)
    assert not db.exec(select(InboxItem)).all()
    now += health.HEARTBEAT_TIMEOUT + timedelta(seconds=1)
    assert health.main() == 1
    dispatch_worker_alerts(db, now)
    assert len(db.exec(select(InboxItem)).all()) == 4


def test_missing_heartbeat_has_startup_grace_and_alerts_once(
    db: Session,
    tenants: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert len(tenants) == 2
    now = utc_now()
    monkeypatch.setattr(monitor, "PROCESS_STARTED_AT", now)
    for elapsed in (timedelta(0), health.HEARTBEAT_TIMEOUT - timedelta(seconds=1)):
        dispatch_worker_alerts(db, now + elapsed)
        assert not db.exec(select(InboxItem)).all()
    for elapsed in (
        health.HEARTBEAT_TIMEOUT,
        health.HEARTBEAT_TIMEOUT + timedelta(minutes=1),
    ):
        dispatch_worker_alerts(db, now + elapsed)
        assert len(db.exec(select(InboxItem)).all()) == 4
    # A stale record is actionable even while this process is still starting.
    db.add(
        WorkerHeartbeat(beat_at=now - health.HEARTBEAT_TIMEOUT - timedelta(seconds=1))
    )
    db.commit()
    dispatch_worker_alerts(db, now)
    assert len(db.exec(select(InboxItem)).all()) == 8


@pytest.mark.parametrize("helper", ["profiles", "recovery"])
def test_manual_worker_does_not_run_hourly_defaults(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
) -> None:
    from tests.api.routes.test_initial_profiles import work_queue
    from tests.test_worker import recover_stalled_jobs

    now = datetime(2026, 11, 15, 1, 1, tzinfo=UTC).timestamp()
    monkeypatch.setattr(periodic, "time", SimpleNamespace(time=lambda: now))
    connector = PsycopgConnector(
        conninfo=engine.url.set(drivername="postgresql").render_as_string(
            hide_password=False
        )
    )

    async def slow_ping() -> None:
        await asyncio.sleep(0.2)

    async def slow_recovery(context: object, timestamp: int) -> None:  # noqa: ARG001
        await asyncio.sleep(0.2)

    monkeypatch.setattr(tasks.ping, "func", slow_ping)
    monkeypatch.setattr(tasks.retry_stalled_jobs, "func", slow_recovery)
    with app.replace_connector(connector):
        if helper == "profiles":
            with app.open():
                tasks.ping.defer()
            work_queue()
        else:
            recover_stalled_jobs()
    jobs = db.execute(text("SELECT task_name FROM procrastinate_jobs")).scalars().all()
    assert "capabilities.fill_department_defaults" not in jobs
    assert jobs == [
        "system.ping" if helper == "profiles" else "system.retry_stalled_jobs"
    ]


@pytest.mark.parametrize("seconds", [None, 0, 240, 301])
def test_health_command_and_overview(
    db: Session,
    client: TestClient,
    tenants: list,
    monkeypatch: pytest.MonkeyPatch,
    seconds: int | None,
) -> None:
    now = utc_now()
    if seconds is not None:
        db.add(WorkerHeartbeat(beat_at=now - timedelta(seconds=seconds)))
        db.commit()
    monkeypatch.setattr(overview, "utc_now", lambda: now)
    result = subprocess.run(
        [sys.executable, "-m", "app.worker.health"],
        capture_output=True,
        text=True,
    )
    healthy = seconds is not None and seconds <= 300
    assert result.returncode == (0 if healthy else 1), result.stderr
    member = tenants[0][0]
    headers = {
        "Authorization": f"Bearer {create_access_token(member.user_id, timedelta(minutes=5))}"
    }
    path = f"/api/v1/orgs/{member.org_id}/admin/overview"
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    worker = next(c for c in response.json()["status_cards"] if c["key"] == "worker")
    assert worker["level"] == ("ok" if healthy else "error")
    assert worker["title"] == "后台任务"
    if not healthy:
        assert "docker compose ps" in str(worker["fix_steps"])
        assert "docker compose restart worker" in str(worker["fix_steps"])
        assert ("5 分 1 秒" if seconds else "尚未收到") in worker["summary"]
    for user, expected in [(tenants[0][2], 403), (tenants[1][0], 404)]:
        headers = {
            "Authorization": f"Bearer {create_access_token(user.user_id, timedelta(minutes=5))}"
        }
        assert client.get(path, headers=headers).status_code == expected
    assert health.is_fresh(now - timedelta(minutes=5), now)


@pytest.mark.parametrize("missing", [False, True])
def test_alerts_are_tenant_scoped_and_deduplicated(
    db: Session,
    tenants: list,
    missing: bool,
) -> None:
    now = utc_now()
    if not missing:
        db.add(WorkerHeartbeat(beat_at=now - timedelta(minutes=6)))
        db.commit()
    dispatch_worker_alerts(db, now)
    with Session(engine) as restarted:
        dispatch_worker_alerts(restarted, now + timedelta(seconds=10))
    for members in tenants:
        items = db.exec(
            select(InboxItem).where(InboxItem.org_id == members[0].org_id)
        ).all()
        assert {item.recipient_user_id for item in items} == {
            m.user_id for m in members[:2]
        }
        assert len(items) == 2
        assert all(
            i.kind == InboxKind.ADMIN_TODOS and i.admin_todo_date is None for i in items
        )
        queued = db.exec(
            select(ImNotification).where(ImNotification.org_id == members[0].org_id)
        ).all()
        assert len(queued) == 1
        assert queued[0].employee_id == members[0].employee_id
        assert queued[0].platform == "feishu" and queued[0].status == "pending"
    row = db.get(WorkerHeartbeat, 1) or WorkerHeartbeat(beat_at=now)
    row.beat_at = now
    db.add(row)
    db.commit()
    dispatch_worker_alerts(db, now)
    for members in tenants:
        assert (
            len(
                db.exec(
                    select(InboxItem).where(InboxItem.org_id == members[0].org_id)
                ).all()
            )
            == 2
        )
    dispatch_worker_alerts(db, now + timedelta(minutes=6))
    for members in tenants:
        items = db.exec(
            select(InboxItem).where(InboxItem.org_id == members[0].org_id)
        ).all()
        assert len(items) == 4


def test_gateway_delivers_alert_and_preserves_digest_copy(
    db: Session,
    tenants: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    member = tenants[0][0]
    adapter = AsyncMock()
    adapters = {(member.org_id, "feishu"): ("synthetic-encrypted-value", adapter)}
    g = gateway.Gateway(engine)
    g.adapters = adapters
    monkeypatch.setattr(g, "refresh", AsyncMock())

    async def stop(seconds: float) -> None:
        assert seconds == 10
        raise asyncio.CancelledError

    monkeypatch.setattr(gateway.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(g.run())
    adapter.send_text.assert_awaited_once_with(
        str(member.user_id), health.WORKER_ALERT_NOTIFICATION
    )
    assert "synthetic" not in adapter.send_text.call_args.args[1]
    add_item(
        db,
        InboxItem(
            org_id=member.org_id,
            recipient_user_id=member.user_id,
            kind=InboxKind.ADMIN_TODOS,
            admin_todo_count=3,
            admin_todo_date=utc_now().date(),
            message="普通待办，不发送正文",
        ),
    )
    db.commit()
    asyncio.run(notifications.send_pending(engine, adapters))
    assert (
        adapter.send_text.call_args.args[1]
        == "你有 3 条管理员待办，请前往网页「协同 → AI 助手（企业管理）」处理。"
    )
    # Queue another outage and revoke the role before delivery.
    db.add(WorkerHeartbeat(beat_at=utc_now() - timedelta(minutes=6)))
    db.commit()
    dispatch_worker_alerts(db, utc_now())
    db.execute(
        delete(RoleGrant).where(
            RoleGrant.org_id == member.org_id, RoleGrant.membership_id == member.id
        )
    )
    db.commit()
    calls = adapter.send_text.await_count
    asyncio.run(notifications.send_pending(engine, adapters))
    assert adapter.send_text.await_count == calls


def test_migration_roundtrip_and_model_registration(migration_engine: Engine) -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.models import SQLModel; assert 'worker_heartbeat' in SQLModel.metadata.tables",
        ],
        check=True,
        capture_output=True,
    )
    migrate(migration_engine, "downgrade", "b617001dc001")
    assert "worker_heartbeat" not in inspect(migration_engine).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    columns = inspect(migration_engine).get_columns("worker_heartbeat")
    assert {column["name"] for column in columns} == {"id", "beat_at"}
    metadata = MetaData()
    WorkerHeartbeat.__table__.to_metadata(metadata)
    with migration_engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "include_name": lambda name, type_, parents: (
                    type_ != "table" or name == "worker_heartbeat"
                ),
            },
        )
        assert compare_metadata(context, metadata) == []


def test_health_storage_failure_does_not_expose_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        health, "Session", Mock(side_effect=SQLAlchemyError("synthetic-secret"))
    )
    assert health.main() == 1
    assert "synthetic-secret" not in capsys.readouterr().err
