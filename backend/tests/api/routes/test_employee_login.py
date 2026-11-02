from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import User, UserCreate
from tests.utils.utils import random_email, random_lower_string


@pytest.mark.parametrize("with_email", [False, True])
def test_login_identifiers(client: TestClient, db: Session, with_email: bool) -> None:
    username = random_lower_string()
    email = random_email() if with_email else None
    password = random_lower_string()
    user = crud.create_user(
        session=db,
        user_create=UserCreate(
            username=f"  {username}  ", email=email, password=password
        ),
    )
    assert user.username == username and user.last_login_at is None
    for identifier in [username, username.upper(), *([email.upper()] if email else [])]:
        response = client.post(
            f"{settings.API_V1_STR}/login/access-token",
            data={"username": identifier, "password": password},
        )
        assert response.status_code == 200
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        public = client.get(f"{settings.API_V1_STR}/users/me", headers=headers).json()
        assert public["username"] == username and public["email"] == email
        assert public["last_login_at"] and public["must_change_password"] is False
    user.is_active = False
    db.add(user)
    db.commit()
    response = client.post(
        f"{settings.API_V1_STR}/login/access-token",
        data={"username": username, "password": password},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "账号已停用，请联系企业管理员"


def test_failed_login_runs_dummy_verification(client: TestClient) -> None:
    with patch("app.crud.verify_password", wraps=crud.verify_password) as verify:
        response = client.post(
            f"{settings.API_V1_STR}/login/access-token",
            data={"username": random_lower_string(), "password": "wrong-password"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "工号/邮箱或密码错误"
        assert verify.call_args.args[1] == crud.DUMMY_HASH


@pytest.mark.parametrize("field", ["username", "email"])
def test_case_insensitive_uniqueness(db: Session, field: str) -> None:
    username, email = random_lower_string(), random_email()
    crud.create_user(
        session=db,
        user_create=UserCreate(
            username=username, email=email, password="test-password"
        ),
    )
    duplicate = User(
        username=random_lower_string(), email=random_email(), hashed_password="unused"
    )
    setattr(duplicate, field, (username if field == "username" else email).upper())
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(duplicate)
        db.flush()


def test_username_required() -> None:
    for username in ["", "  ", "x" * 256, None, 123, {}]:
        with pytest.raises(ValidationError):
            UserCreate.model_validate(
                {"username": username, "password": "test-password"}
            )
    with pytest.raises(ValidationError):
        UserCreate.model_validate({"password": "test-password"})


@pytest.mark.parametrize(
    "path",
    [
        "users/signup",
        "password-recovery/test@example.com",
        "reset-password/",
        "password-recovery-html-content/test@example.com",
    ],
)
def test_removed_routes(client: TestClient, path: str) -> None:
    assert client.post(f"{settings.API_V1_STR}/{path}", json={}).status_code == 404


@pytest.mark.parametrize(
    "identifier",
    [
        "admin",
        "E01",
        "e02",
        "E05",
        "MANAGER.PLATFORM@DEMO.EXAMPLE.COM",
        "EMPLOYEE.PLATFORM@DEMO.EXAMPLE.COM",
        "EMPLOYEE.ALGORITHM@DEMO.EXAMPLE.COM",
        "ADMIN@DEMO.EXAMPLE.COM",
    ],
)
def test_demo_login(client: TestClient, identifier: str) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/login/access-token",
        data={"username": identifier, "password": settings.DEMO_USER_PASSWORD},
    )
    assert response.status_code == 200
