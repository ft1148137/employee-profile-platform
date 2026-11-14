from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, select

from app.core.db import engine
from app.domains.admin_assistant import store
from app.domains.audit.models import AuditEvent
from app.domains.inbox import admin_todos
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.org.models import Organization
from app.domains.review import direct
from app.domains.source.models import SyncRun
from app.domains.work.records import COLUMNS, EXAMPLE_ROW, parse_work_records
from app.worker.app import app as worker_app
from app.worker.tasks import dispatch_admin_digest
from tests.admin_assistant.test_registry import principal as admin_principal
from tests.api.routes.test_direct_profile_edits import edit_body
from tests.domains.test_admin_todos import bind_admin
from tests.domains.test_work_records import csv_bytes
from tests.utils.migrations import migrate


def test_timezone_settings_and_audit(
    demo: tuple, db: Session, client: TestClient
) -> None:
    base, people, auth = demo
    org_id = people["E02"].org_id
    url = f"{base}/update-settings/timezone"
    response = client.get(url, headers=auth["admin"])
    assert response.status_code == 200
    assert response.json() == {"timezone": "Asia/Shanghai"}
    for role, status in (("manager", 403), ("employee", 403), ("foreign", 404)):
        assert client.get(url, headers=auth[role]).status_code == status
        assert (
            client.put(
                url, headers=auth[role], json={"timezone": "Asia/Tokyo"}
            ).status_code
            == status
        )
    for value in ("", "Invalid/Zone", "../UTC", "/etc/passwd", "x" * 101):
        assert (
            client.put(url, headers=auth["admin"], json={"timezone": value}).status_code
            == 422
        )
    body = {"timezone": "Asia/Tokyo"}
    for _ in range(2):
        saved = client.put(url, headers=auth["admin"], json=body)
        assert saved.status_code == 200 and saved.json() == body
    assert client.get(url, headers=auth["admin"]).json() == body
    audits = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org_id, AuditEvent.action == "org.timezone_updated"
        )
    ).all()
    assert len(audits) == 1
    assert audits[0].details == {
        "before": {"timezone": "Asia/Shanghai"},
        "after": body,
    }
    foreign = db.exec(select(Organization).where(Organization.id != org_id)).first()
    assert foreign is not None and foreign.timezone == "Asia/Shanghai"
    # The assistant's existing reminder settings must not accept a timezone.
    assert (
        client.put(
            f"{base}/update-settings", headers=auth["admin"], json=body
        ).status_code
        == 422
    )


def test_direct_notification_uses_org_timezone(
    demo: tuple, db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, people, auth = demo
    subject = people["E02"]
    assert (
        client.put(
            f"{base}/update-settings/timezone",
            headers=auth["admin"],
            json={"timezone": "Asia/Tokyo"},
        ).status_code
        == 200
    )
    monkeypatch.setattr(
        direct, "utc_now", lambda: datetime(2026, 1, 2, 16, 30, tzinfo=UTC)
    )
    response = client.post(
        f"{base}/employees/{subject.id}/profile/direct-edits",
        headers=auth["admin"],
        json=edit_body(db, subject),
    )
    assert response.status_code == 200
    notice = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == subject.org_id,
            InboxItem.recipient_employee_id == subject.id,
            InboxItem.kind == InboxKind.PROFILE_EDITED,
        )
    ).one()
    assert "2026-01-03 01:30" in notice.message


def test_digest_uses_local_hour_and_day(demo: tuple, db: Session) -> None:
    principal, _ = bind_admin(db, demo)
    org = db.get(Organization, principal.org_id)
    assert org is not None
    org.timezone = "Asia/Tokyo"
    db.add(org)
    db.commit()

    def items() -> list[InboxItem]:
        return list(
            db.exec(
                select(InboxItem).where(
                    InboxItem.org_id == org.id, InboxItem.kind == InboxKind.ADMIN_TODOS
                )
            ).all()
        )

    now = datetime(2026, 1, 2, tzinfo=UTC)
    admin_todos.dispatch_admin_todos(db, now - timedelta(hours=1))
    assert not items()

    def dispatch(_: int) -> None:
        with Session(engine) as session:
            admin_todos.dispatch_admin_todos(session, now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(dispatch, range(2)))
    admin_todos.dispatch_admin_todos(db, now + timedelta(minutes=30))
    assert [i.admin_todo_date for i in items()] == [date(2026, 1, 2)]
    admin_todos.dispatch_admin_todos(db, now + timedelta(hours=1))
    assert len(items()) == 1
    # Local date can differ from UTC; use it for the daily deduplication key.
    org.timezone = "Pacific/Auckland"
    db.add(org)
    db.commit()
    admin_todos.dispatch_admin_todos(db, datetime(2026, 1, 2, 20, tzinfo=UTC))
    assert {i.admin_todo_date for i in items()} == {date(2026, 1, 2), date(2026, 1, 3)}


def test_digest_worker_runs_hourly(demo: tuple, db: Session) -> None:
    p, _ = bind_admin(db, demo)
    org = db.get(Organization, p.org_id)
    assert org is not None
    org.timezone = "Asia/Tokyo"
    db.add(org)
    db.commit()
    assert (
        worker_app.periodic_registry.periodic_tasks[("admin.dispatch_todos", "")].cron
        == "0 * * * *"
    )
    assert dispatch_admin_digest.queueing_lock == "admin_dispatch_todos"
    dispatch_admin_digest(timestamp=int(datetime(2026, 1, 2, tzinfo=UTC).timestamp()))
    assert db.exec(
        select(InboxItem.id).where(
            InboxItem.org_id == p.org_id, InboxItem.kind == InboxKind.ADMIN_TODOS
        )
    ).one()


def test_import_and_assistant_preview_use_org_timezone(
    demo: tuple, db: Session, client: TestClient
) -> None:
    base, people, auth = demo
    org_id = people["E02"].org_id
    assert (
        client.put(
            f"{base}/update-settings/timezone",
            headers=auth["admin"],
            json={"timezone": "Asia/Tokyo"},
        ).status_code
        == 200
    )
    row = list(EXAMPLE_ROW)
    row[3] = "2026-01-02 09:30"
    content = csv_bytes([list(COLUMNS), row])
    sid = store.create_session(db, admin_principal(db, demo)).id
    preview = client.post(
        f"/api/v1/admin-assistant/sessions/{sid}/files",
        params={"org_id": str(org_id)},
        headers=auth["admin"],
        data={"kind": "work_csv"},
        files={"file": ("work.csv", content)},
    )
    assert preview.status_code == 200
    warning = preview.json()["messages"][0]["preview"]["warnings"][0]["message"]
    assert "Asia/Tokyo（+09:00）" in warning
    imported = client.post(
        f"{base}/sources/csv/imports",
        headers=auth["admin"],
        files={"file": ("work.csv", content)},
    )
    assert imported.status_code == 202
    assert imported.json()["issues"][0]["message"] == warning
    run = db.exec(select(SyncRun).where(SyncRun.org_id == org_id)).one()
    assert run.input is not None
    assert run.input["rows"][0]["occurred_at"] == "2026-01-02T09:30:00+09:00"


@pytest.mark.parametrize(
    ("timezone", "value", "hours", "warning"),
    [
        ("Asia/Shanghai", "2026-01-02 09:30", 8, "北京时间（+08:00）"),
        ("Asia/Tokyo", "2026-01-02 09:30", 9, "Asia/Tokyo（+09:00）"),
        ("America/New_York", "2026-01-02 09:30", -5, "America/New_York（-05:00）"),
        ("America/New_York", "2026-07-02 09:30", -4, "America/New_York（-04:00）"),
        ("Asia/Tokyo", "2026-01-02T09:30:00+03:00", 3, ""),
    ],
)
def test_record_timezone_offsets(
    timezone: str, value: str, hours: int, warning: str
) -> None:
    row = list(EXAMPLE_ROW)
    row[3] = value
    result = parse_work_records(
        csv_bytes([list(COLUMNS), row]), "work.csv", timezone=timezone
    )
    assert not result.has_errors
    assert result.rows[0].occurred_at.utcoffset() == timedelta(hours=hours)
    if warning:
        assert warning in result.issues[0].message
    else:
        assert not result.issues


def test_timezone_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b615001dc001")
    assert "timezone" not in {
        c["name"] for c in inspect(migration_engine).get_columns("organization")
    }
    with migration_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization (id, slug, name, created_at) "
                "VALUES ('00000000-0000-0000-0000-000000000617', 'timezone-test', 'Synthetic', now())"
            )
        )
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT timezone FROM organization WHERE slug = 'timezone-test'")
            ).scalar_one()
            == "Asia/Shanghai"
        )
    migrate(migration_engine, "downgrade", "-1")
    migrate(migration_engine, "upgrade", "head")
    column = next(
        c
        for c in inspect(migration_engine).get_columns("organization")
        if c["name"] == "timezone"
    )
    assert not column["nullable"]
