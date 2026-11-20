import hashlib
import json
import shutil
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import Engine, delete, inspect
from sqlmodel import Session, select

from app.core import security
from app.core.config import settings
from app.core.db import engine, init_db
from app.core.security import create_access_token
from app.domains.account.models import LoginAttempt
from app.domains.org.models import Employee, Organization, OrgMembership
from app.main import app
from app.models import User
from app.seed.loader import load_demo
from tests.utils.demo import DemoSeed
from tests.utils.drafting import mock_drafting  # noqa: F401
from tests.utils.im import telegram_server  # noqa: F401
from tests.utils.migrations import MIGRATION_TEMPLATE, isolated_database
from tests.utils.org import ORG_MODELS, cleanup_new_users, delete_test_organization
from tests.utils.pi import _pi_process, pi_service  # noqa: F401
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers

_PRODUCTION_PASSWORD_HASH = security.password_hash


@pytest.fixture(scope="session")
def production_password_hash() -> PasswordHash:
    return _PRODUCTION_PASSWORD_HASH


@pytest.fixture(scope="session", autouse=True)
def _fast_password_hash() -> Generator[None]:
    security.password_hash = PasswordHash(
        (
            Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1),
            BcryptHasher(rounds=4),
        )
    )
    try:
        yield
    finally:
        security.password_hash = _PRODUCTION_PASSWORD_HASH


@pytest.fixture
def reset_login_attempts(db: Session) -> Generator[None]:
    db.execute(delete(LoginAttempt))
    db.commit()
    yield
    db.execute(delete(LoginAttempt))
    db.commit()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "migration_engine" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.migration)


@pytest.fixture(scope="session")
def migration_template(pytestconfig: pytest.Config) -> str:
    return pytestconfig.stash[MIGRATION_TEMPLATE]


@pytest.fixture
def migration_engine(migration_template: str) -> Generator[Engine]:
    with isolated_database(
        engine.url, f"migration_test_{uuid4().hex}", template=migration_template
    ) as isolated:
        yield isolated


@pytest.fixture(scope="session")
def _demo_seed(_fast_password_hash: None) -> DemoSeed:
    with Session(engine) as initial:
        init_db(initial)
    with Session(engine) as seed:
        load_demo(seed, settings.DEMO_DATA_DIR)
        return DemoSeed.capture(seed)


@pytest.fixture(scope="session", autouse=True)
def db(_demo_seed: DemoSeed) -> Generator[Session]:
    with Session(engine) as session:
        existing_user_ids = set(session.exec(select(User.id)).all())
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


def _copy_seed(tmp_path: Path) -> Path:
    path = tmp_path / "examples/demo-org"
    shutil.copytree(settings.DEMO_DATA_DIR, path)
    shutil.copytree(
        settings.DEMO_DATA_DIR.parent.parent / "templates", tmp_path / "templates"
    )
    org = yaml.safe_load((path / "org.yaml").read_text())
    org["slug"] = f"seed-test-{uuid4()}"
    (path / "org.yaml").write_text(yaml.safe_dump(org, allow_unicode=True))
    return path


@pytest.fixture
def seed_path(tmp_path: Path) -> Generator[Path]:
    path = _copy_seed(tmp_path)
    slug = yaml.safe_load((path / "org.yaml").read_text())["slug"]
    yield path
    with Session(engine) as session:
        existing = session.exec(
            select(Organization).where(Organization.slug == slug)
        ).one_or_none()
        if existing:
            delete_test_organization(session, existing.id)


@contextmanager
def _demo(
    db: Session,
    seed: DemoSeed,
    *,
    token_minutes: int = 5,
    minimal: bool = False,
    local_accounts: bool = False,
) -> Generator[tuple[tuple, list[UUID], list[UUID]]]:
    foreign = Organization(slug=str(uuid4()), name="Synthetic foreign tenant")
    foreign_user = User(
        username=str(uuid4()), email=f"{uuid4()}@example.com", hashed_password="unused"
    )
    foreign_id = foreign.id
    with Session(engine) as setup:
        org_id, accounts = seed.clone(
            setup, minimal=minimal, local_accounts=local_accounts
        )
        setup.add_all([foreign, foreign_user])
        setup.flush()
        setup.add(OrgMembership(org_id=foreign_id, user_id=foreign_user.id))
        foreign_user_id = foreign_user.id
        setup.commit()
    org_ids = [org_id, foreign_id]
    try:
        employees = {
            e.employee_code: e
            for e in db.exec(select(Employee).where(Employee.org_id == org_id)).all()
        }
        auth = {
            role: {
                "Authorization": f"Bearer {create_access_token(user_id, expires_delta=timedelta(minutes=token_minutes))}"
            }
            for role, user_id in {
                "admin": accounts["admin@demo.example.com"],
                "manager": accounts["manager.platform@demo.example.com"],
                "employee": accounts["employee.platform@demo.example.com"],
                "foreign": foreign_user_id,
            }.items()
        }
        user_ids = [
            uid
            for username, uid in accounts.items()
            if username.endswith("@demo.example.com")
        ]
        yield (
            (f"/api/v1/orgs/{org_id}", employees, auth),
            org_ids,
            [*user_ids, foreign_user_id],
        )
    finally:
        db.rollback()
        delete_test_organization(db, *org_ids)


@pytest.fixture
def demo(db: Session, _demo_seed: DemoSeed) -> Generator[tuple]:
    with _demo(db, _demo_seed) as (data, _, _):
        yield data


@pytest.fixture
def minimal_demo(db: Session, _demo_seed: DemoSeed) -> Generator[tuple]:
    with _demo(db, _demo_seed, minimal=True) as (data, _, _):
        yield data


@pytest.fixture
def isolated_demo(db: Session, _demo_seed: DemoSeed) -> Generator[tuple]:
    with _demo(db, _demo_seed, token_minutes=10, local_accounts=True) as (data, _, _):
        yield data


def _demo_snapshot(
    session: Session, org_ids: list[UUID], user_ids: list[UUID]
) -> dict[str, str]:
    queries = {
        str(model.__tablename__): select(model)
        .where(model.org_id.in_(org_ids))
        .order_by(*inspect(model).primary_key)
        for model in ORG_MODELS
    }
    queries["organization"] = (
        select(Organization)
        .where(Organization.id.in_(org_ids))
        .order_by(Organization.id)
    )
    contents = {
        name: [row.model_dump(mode="json") for row in session.exec(query).all()]
        for name, query in queries.items()
    }
    contents["user"] = [
        {
            "id": str(row.id),
            "is_active": row.is_active,
            "password_changed_at": str(row.password_changed_at),
        }
        for row in session.exec(
            select(User).where(User.id.in_(user_ids)).order_by(User.id)
        ).all()
    ]
    return {
        name: hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        for name, rows in contents.items()
    }


@pytest.fixture(scope="session")
def shared_demo(db: Session, _demo_seed: DemoSeed) -> Generator[tuple]:
    with _demo(db, _demo_seed, token_minutes=60) as (data, org_ids, user_ids):
        before = _demo_snapshot(db, org_ids, user_ids)
        try:
            yield data
        finally:
            db.rollback()
            after = _demo_snapshot(db, org_ids, user_ids)
            changed = [name for name in before if before[name] != after[name]]
            if changed:
                pytest.fail(f"shared_demo was modified: {', '.join(changed)}")
