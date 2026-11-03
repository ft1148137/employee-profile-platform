import subprocess
import sys
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlmodel import Session, select

from app.cli.__main__ import main
from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.domains.audit.models import ActorKind, AuditEvent
from app.domains.base import utc_now
from app.domains.org.models import OrgMembership
from tests.authz.test_tenant_access import headers, tenant_data  # noqa: F401
from tests.utils.migrations import migrate


@pytest.mark.parametrize("memberships", [0, 1, 2])
def test_reset_password(
    request: pytest.FixtureRequest,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    memberships: int,
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    user = users[5]
    user.username = f"Cli-{user.id}"
    old_password = "Old-password-123"
    user.hashed_password = get_password_hash(old_password)
    user.password_changed_at = utc_now() - timedelta(minutes=1)
    db.add(user)
    db.add_all(
        OrgMembership(org_id=org.id, user_id=user.id) for org in orgs[:memberships]
    )
    db.commit()
    old_auth = headers(user)

    def login(password: str) -> dict:
        response = client.post(
            "/api/v1/login/access-token",
            data={"username": user.username, "password": password},
        )
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    for _ in range(settings.LOGIN_MAX_FAILURES):
        assert (
            client.post(
                "/api/v1/login/access-token",
                data={"username": user.username, "password": "wrong"},
            ).status_code
            == 400
        )
    monkeypatch.setattr(
        sys, "argv", ["app.cli", "reset-password", user.username.upper()]
    )
    assert main() == 0
    output = capsys.readouterr()
    password = output.out.splitlines()[1].removeprefix("新密码：")
    assert output.out == f"登录名：{user.username}\n新密码：{password}\n"
    assert output.err == "" and password not in caplog.text
    db.refresh(user)
    assert user.must_change_password and user.password_changed_at is not None
    assert not verify_password(old_password, user.hashed_password)[0]
    assert (
        client.post(
            "/api/v1/login/access-token",
            data={"username": user.username, "password": old_password},
        ).status_code
        == 400
    )
    assert client.get("/api/v1/users/me", headers=old_auth).status_code == 401
    auth = login(password)
    assert client.get("/api/v1/users/me", headers=auth).json()["must_change_password"]
    response = client.get("/api/v1/me/memberships", headers=auth)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "password_change_required"
    events = db.exec(
        select(AuditEvent).where(AuditEvent.target_id == str(user.id))
    ).all()
    assert len(events) == max(1, memberships)
    assert {e.org_id for e in events} == (
        {o.id for o in orgs[:memberships]} if memberships else {None}
    )
    for event in events:
        assert event.actor_type == ActorKind.CLI and event.actor_id is None
        assert event.action == "account.password_reset" and event.target_type == "user"
        assert event.details == {"user_id": str(user.id), "source": "cli"}
        assert password not in str(event.model_dump())


def test_disabled_and_missing(
    request: pytest.FixtureRequest, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    user = request.getfixturevalue("tenant_data")[3][4]
    user.is_active = False
    db.add(user)
    db.commit()
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "reset-password", user.username],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stderr.endswith("这个账号已停用，重置密码不会启用账号\n")
    password = result.stdout.splitlines()[1].removeprefix("新密码：")
    assert password not in result.stderr and password not in caplog.text
    db.refresh(user)
    assert not user.is_active and verify_password(password, user.hashed_password)[0]
    missing = f"missing-{user.id}"
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "reset-password", missing],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and result.stdout == ""
    assert result.stderr.endswith(f"找不到登录名 {missing}\n")


def test_cli_actor_migration(migration_engine: Engine) -> None:
    for action, revision in (("downgrade", "c20cd19c259b"), ("upgrade", "head")):
        migrate(migration_engine, action, revision)
    with Session(migration_engine) as session:
        event = AuditEvent(
            actor_type=ActorKind.CLI,
            actor_id=None,
            action="account.password_reset",
            target_type="user",
            details={"source": "cli"},
        )
        session.add(event)
        session.commit()
        event_id = event.id
        head = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    with pytest.raises(ValueError, match="CLI audit events exist"):
        migrate(migration_engine, "downgrade", "c20cd19c259b")
    with Session(migration_engine) as session:
        assert session.get(AuditEvent, event_id).actor_type == ActorKind.CLI
        assert (
            session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == head
        )
