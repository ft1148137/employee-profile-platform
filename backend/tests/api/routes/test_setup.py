import hashlib
import subprocess
import sys
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect
from sqlmodel import Session, select

from app.api.deps import get_db
from app.cli import setup_link as cli
from app.cli.__main__ import main
from app.core.config import settings
from app.core.security import verify_password
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.org.models import Organization
from app.domains.setup import service
from app.domains.setup.models import SetupPurpose, SetupToken
from app.domains.setup.schemas import InitializeInput
from app.main import app
from app.models import User

PREFIX = settings.API_V1_STR
PAYLOAD = {
    "org_name": "  合成测试企业  ",
    "admin_username": "  SetupAdmin  ",
    "admin_password": "synthetic-password-2026",
    "admin_full_name": "测试管理员",
}


@pytest.fixture
def setup_db(migration_engine: Engine) -> Generator[tuple[TestClient, Session]]:
    def isolated_db() -> Generator[Session]:
        with Session(migration_engine) as session:
            yield session

    app.dependency_overrides[get_db] = isolated_db
    try:
        with TestClient(app) as client, Session(migration_engine) as session:
            yield client, session
    finally:
        app.dependency_overrides.pop(get_db)


def issue(client: TestClient, purpose: str = "initial_setup") -> str:
    response = client.post(f"{PREFIX}/private/setup-tokens", json={"purpose": purpose})
    assert response.status_code == 200
    return str(response.json()["token"])


def test_initialize_and_authentication(setup_db: tuple[TestClient, Session]) -> None:
    client, session = setup_db
    assert client.get(f"{PREFIX}/setup/status").json() == {"initialized": False}
    token = issue(client)
    stored = session.exec(select(SetupToken)).one()
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert timedelta(hours=71) < stored.expires_at - utc_now() <= timedelta(hours=72)
    response = client.post(
        f"{PREFIX}/setup/initialize", json={**PAYLOAD, "token": token}
    )
    assert response.status_code == 200
    result = response.json()
    headers = {"Authorization": f"Bearer {result['access_token']}"}
    assert (
        client.get(
            f"{PREFIX}/orgs/{result['org_id']}/departments", headers=headers
        ).status_code
        == 200
    )
    membership = client.get(f"{PREFIX}/me/memberships", headers=headers).json()[0]
    assert membership["roles"] == ["org_admin"]
    assert membership["employee_id"] is None
    assert membership["org"]["name"] == PAYLOAD["org_name"].strip()
    user = session.exec(select(User)).one()
    assert user.username == "SetupAdmin"
    assert not user.is_superuser and not user.must_change_password
    assert user.password_changed_at is not None
    assert verify_password(PAYLOAD["admin_password"], user.hashed_password)[0]
    audit = session.exec(select(AuditEvent)).one()
    assert audit.action == "setup.initialized"
    assert audit.actor_type == "user" and audit.actor_id == str(user.id)
    assert audit.details == {"purpose": "initial_setup"}
    session.refresh(stored)
    assert stored.used_at is not None
    assert client.get(f"{PREFIX}/setup/status").json() == {"initialized": True}


@pytest.mark.parametrize("state", ["rotated", "expired", "used", "missing"])
def test_invalid_tokens_share_error(
    setup_db: tuple[TestClient, Session], state: str
) -> None:
    client, session = setup_db
    token = issue(client)
    stored = session.exec(select(SetupToken)).one()
    if state == "rotated":
        issue(client)
        session.refresh(stored)
        assert stored.used_at is None and stored.expires_at <= utc_now()
    elif state == "expired":
        stored.expires_at = utc_now() - timedelta(seconds=1)
        session.add(stored)
        session.commit()
    elif state == "used":
        assert (
            client.post(
                f"{PREFIX}/setup/initialize", json={**PAYLOAD, "token": token}
            ).status_code
            == 200
        )
    else:
        token = "does-not-exist"
    response = client.post(
        f"{PREFIX}/setup/initialize", json={**PAYLOAD, "token": token}
    )
    assert response.status_code == 400
    assert response.json() == {
        "detail": "初始化链接无效或已过期，请联系部署人员重新获取"
    }


def test_existing_org_and_new_org(setup_db: tuple[TestClient, Session]) -> None:
    client, session = setup_db
    initial = issue(client)
    new_org = issue(client, "new_org")
    assert (
        client.post(
            f"{PREFIX}/setup/initialize", json={**PAYLOAD, "token": new_org}
        ).status_code
        == 200
    )
    response = client.post(
        f"{PREFIX}/setup/initialize", json={**PAYLOAD, "token": initial}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "企业已经初始化，请用管理员账号登录"
    assert (
        session.exec(
            select(SetupToken).where(SetupToken.purpose == SetupPurpose.INITIAL_SETUP)
        )
        .one()
        .used_at
        is None
    )
    assert (
        client.post(
            f"{PREFIX}/private/setup-tokens", json={"purpose": "initial_setup"}
        ).status_code
        == 409
    )
    next_token = issue(client, "new_org")
    assert (
        client.post(
            f"{PREFIX}/setup/initialize",
            json={**PAYLOAD, "admin_username": "second", "token": next_token},
        ).status_code
        == 200
    )
    assert len(session.exec(select(Organization)).all()) == 2


def test_admin_username_cannot_contain_at(setup_db: tuple[TestClient, Session]) -> None:
    client, session = setup_db
    token = issue(client)
    response = client.post(
        f"{PREFIX}/setup/initialize",
        json={**PAYLOAD, "admin_username": "it@example.com", "token": token},
    )
    assert response.status_code == 400
    assert response.json() == {
        "detail": "管理员登录名不能包含 @，请用字母或数字，例如 admin"
    }
    assert not session.exec(select(Organization)).all()
    assert not session.exec(select(User)).all()
    assert not session.exec(select(AuditEvent)).all()
    assert session.exec(select(SetupToken)).one().used_at is None
    assert (
        client.post(
            f"{PREFIX}/setup/initialize", json={**PAYLOAD, "token": token}
        ).status_code
        == 200
    )


def test_username_conflict_is_atomic(setup_db: tuple[TestClient, Session]) -> None:
    client, session = setup_db
    session.add(User(username="setupadmin", hashed_password="unused"))
    session.commit()
    token = issue(client)
    response = client.post(
        f"{PREFIX}/setup/initialize", json={**PAYLOAD, "token": token}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "这个登录名已经被使用，请换一个"
    assert not session.exec(select(Organization)).all()
    assert not session.exec(select(AuditEvent)).all()
    assert session.exec(select(SetupToken)).one().used_at is None
    assert (
        client.post(
            f"{PREFIX}/setup/initialize",
            json={**PAYLOAD, "admin_username": "available", "token": token},
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("org_name", "  "),
        ("org_name", "中" * 101),
        ("admin_username", "  "),
        ("admin_password", "short"),
        ("admin_password", "x" * 129),
    ],
)
def test_validation(
    setup_db: tuple[TestClient, Session], field: str, value: str
) -> None:
    client, _ = setup_db
    response = client.post(
        f"{PREFIX}/setup/initialize", json={**PAYLOAD, "token": "token", field: value}
    )
    assert response.status_code == 422


def test_failure_rolls_back(
    setup_db: tuple[TestClient, Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, session = setup_db
    token = issue(client)

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Synthetic audit failure")

    monkeypatch.setattr(service, "record_audit", fail_audit)
    with pytest.raises(RuntimeError, match="Synthetic audit failure"):
        client.post(f"{PREFIX}/setup/initialize", json={**PAYLOAD, "token": token})
    assert not session.exec(select(Organization)).all()
    assert not session.exec(select(User)).all()
    assert session.exec(select(SetupToken)).one().used_at is None


def test_concurrent_token_use(migration_engine: Engine) -> None:
    with Session(migration_engine) as session:
        token = service.issue_token(session, SetupPurpose.INITIAL_SETUP)
        session.commit()
    barrier = Barrier(2)

    def initialize_once() -> int:
        with Session(migration_engine) as session:
            barrier.wait(timeout=10)
            try:
                service.initialize(session, InitializeInput(**PAYLOAD, token=token))
                session.commit()
                return 200
            except service.InvalidSetupToken:
                session.rollback()
                return 400

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: initialize_once(), range(2))) == [200, 400]
    with Session(migration_engine) as session:
        assert len(session.exec(select(Organization)).all()) == 1
        assert len(session.exec(select(User)).all()) == 1


def test_slug_collision_retries(
    setup_db: tuple[TestClient, Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, session = setup_db
    session.add(Organization(slug="org-collision", name="合成现有企业"))
    session.commit()
    slugs = iter(["collision", "unique"])
    monkeypatch.setattr(service.secrets, "token_hex", lambda _: next(slugs))
    token = issue(client, "new_org")
    response = client.post(
        f"{PREFIX}/setup/initialize", json={**PAYLOAD, "token": token}
    )
    assert response.status_code == 200
    assert (
        session.get(Organization, UUID(response.json()["org_id"])).slug == "org-unique"
    )


def test_cli(
    setup_db: tuple[TestClient, Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, session = setup_db
    monkeypatch.setattr(cli, "engine", session.get_bind())
    monkeypatch.setattr(
        sys, "argv", ["app.cli", "setup-link", "--base-url", "https://example.com///"]
    )
    assert main() == 0
    output = capsys.readouterr()
    assert output.err == "" and output.out.startswith(
        "https://example.com/setup#token="
    )
    assert len(output.out.splitlines()) == 1
    token = output.out.strip().split("#token=")[1]
    assert (
        client.post(
            f"{PREFIX}/setup/initialize", json={**PAYLOAD, "token": token}
        ).status_code
        == 200
    )
    assert main() == 2
    output = capsys.readouterr()
    assert output.out == "" and output.err.strip() == "企业已经初始化，不需要初始化链接"
    monkeypatch.setattr(sys, "argv", ["app.cli", "setup-link", "--new-org"])
    assert main() == 0
    assert capsys.readouterr().out.startswith(
        settings.FRONTEND_HOST.rstrip("/") + "/setup#token="
    )


def test_migration_roundtrip(migration_engine: Engine) -> None:
    assert "setup_token" in inspect(migration_engine).get_table_names()
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "d73e295a94f6-1"], check=True
    )
    assert "setup_token" not in inspect(migration_engine).get_table_names()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    assert "setup_token" in inspect(migration_engine).get_table_names()
