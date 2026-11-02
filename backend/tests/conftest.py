import subprocess
import sys
from collections.abc import Generator
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import Engine, create_engine
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine, init_db
from app.main import app
from app.models import User
from tests.utils.org import cleanup_new_users
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers


@pytest.fixture
def migration_engine(monkeypatch: pytest.MonkeyPatch) -> Generator[Engine]:
    # Downgrade tests must never remove the shared database's demo organization.
    database = f"migration_test_{uuid4().hex}"
    url = engine.url.set(database=database)
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    admin_url = engine.url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        isolated = create_engine(url)
        try:
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"], check=True
            )
            yield isolated
        finally:
            isolated.dispose()
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session]:
    with Session(engine) as session:
        existing_user_ids = set(session.exec(select(User.id)).all())
        init_db(session)
        yield session
        session.rollback()
        cleanup_new_users(session, existing_user_ids)
        session.commit()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )
