from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect
from sqlmodel import Session, select
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import crud
from app.core.config import settings
from app.core.security import ALGORITHM, create_access_token
from app.domains.account.models import LoginAttempt
from app.main import app
from app.models import User, UserCreate
from tests.utils.migrations import migrate
from tests.utils.utils import random_lower_string

URL = settings.API_V1_STR


def login(client: TestClient, identifier: str, password: str = "wrong-password") -> int:
    return client.post(
        f"{URL}/login/access-token", data={"username": identifier, "password": password}
    ).status_code


@pytest.fixture
def account(db: Session) -> User:
    return crud.create_user(
        session=db,
        user_create=UserCreate(
            username=random_lower_string(), password="initial-password"
        ),
    )


def test_required_change_and_token_revocation(
    client: TestClient, db: Session, account: User
) -> None:
    account.must_change_password = True
    db.add(account)
    db.commit()
    token = client.post(
        f"{URL}/login/access-token",
        data={"username": account.username, "password": "initial-password"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get(f"{URL}/users/me", headers=headers).json()["must_change_password"]
    response = client.get(f"{URL}/me/memberships", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "password_change_required"
    changed = client.patch(
        f"{URL}/users/me/password",
        headers=headers,
        json={
            "current_password": "initial-password",
            "new_password": "replacement-password",
        },
    )
    assert changed.status_code == 200
    assert client.get(f"{URL}/users/me", headers=headers).status_code == 401
    new_headers = {"Authorization": f"Bearer {changed.json()['access_token']}"}
    assert (
        client.get(f"{URL}/users/me", headers=new_headers).json()[
            "must_change_password"
        ]
        is False
    )
    assert client.get(f"{URL}/me/memberships", headers=new_headers).status_code == 200
    assert login(client, account.username, "replacement-password") == 200
    # A reset within the token's signing second must revoke it as well.
    db.refresh(account)
    account.password_changed_at += timedelta(microseconds=1)
    db.add(account)
    db.commit()
    assert client.get(f"{URL}/users/me", headers=new_headers).status_code == 401


@pytest.mark.parametrize(
    "new_password", ["initial-password", "username", "short", "x" * 129]
)
def test_invalid_new_password(
    client: TestClient, account: User, new_password: str
) -> None:
    headers = {
        "Authorization": f"Bearer {create_access_token(account.id, timedelta(minutes=1))}"
    }
    response = client.patch(
        f"{URL}/users/me/password",
        headers=headers,
        json={
            "current_password": "initial-password",
            "new_password": account.username.upper()
            if new_password == "username"
            else new_password,
        },
    )
    assert response.status_code == (
        422 if new_password in ("short", "x" * 129) else 400
    )


@pytest.mark.parametrize("identifier_kind", ["existing", "missing", "oversized"])
def test_identifier_lock(
    client: TestClient, db: Session, account: User, identifier_kind: str
) -> None:
    identifier = (
        account.username
        if identifier_kind == "existing"
        else ("x" * 1000 if identifier_kind == "oversized" else random_lower_string())
    )
    for _ in range(5):
        assert login(client, f" {identifier.upper()} ") == 400
    with patch("app.crud.authenticate") as authenticate:
        assert login(client, identifier, "initial-password") == 429
        authenticate.assert_not_called()
    if identifier_kind == "existing":
        account.password_changed_at = datetime.now(UTC)
        db.add(account)
        db.commit()
        assert login(client, identifier, "initial-password") == 200


def test_ip_lock_ignores_forged_forwarded_headers(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LOGIN_MAX_FAILURES_PER_IP", 3)
    # Match fastapi run's HTTP proxy policy: external peers are not trusted.
    with TestClient(
        ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1"),
        client=("198.51.100.2", 1234),
    ) as client:
        for index in range(4):
            response = client.post(
                f"{URL}/login/access-token",
                data={"username": random_lower_string(), "password": "wrong-password"},
                headers={"X-Forwarded-For": f"203.0.113.{index}"},
            )
            assert response.status_code == (400 if index < 3 else 429)
    assert {row.ip for row in db.exec(select(LoginAttempt)).all()} == {"198.51.100.2"}


def test_login_window_success_reset_and_cleanup(
    client: TestClient, db: Session, account: User
) -> None:
    now = datetime.now(UTC)
    for days in (2, 0):
        for _ in range(5):
            db.add(
                LoginAttempt(
                    identifier_norm=account.username,
                    ip="testclient",
                    succeeded=False,
                    created_at=now - timedelta(days=days, minutes=16),
                )
            )
    db.commit()
    assert login(client, account.username, "initial-password") == 200
    assert all(
        row.created_at > now - timedelta(days=1)
        for row in db.exec(select(LoginAttempt)).all()
    )
    db.refresh(account)
    for _ in range(5):
        db.add(
            LoginAttempt(
                identifier_norm=account.username,
                ip="testclient",
                succeeded=False,
                created_at=account.last_login_at - timedelta(seconds=1),
            )
        )
    db.commit()
    assert login(client, account.username, "initial-password") == 200


def test_invalid_tokens(client: TestClient, db: Session, account: User) -> None:
    for payload in (
        {"sub": "invalid", "iat": 1},
        {"sub": str(account.id)},
        {"sub": str(account.id), "iat": 1},
    ):
        account.password_changed_at = datetime.now(UTC)
        db.add(account)
        db.commit()
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)
        assert (
            client.get(
                f"{URL}/users/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 401
        )


def test_login_attempt_migration(migration_engine: Engine) -> None:
    assert inspect(migration_engine).has_table("login_attempt")
    migrate(migration_engine, "downgrade", "a51b093e72c4")
    assert not inspect(migration_engine).has_table("login_attempt")
    migrate(migration_engine, "upgrade", "head")
    assert inspect(migration_engine).has_table("login_attempt")
