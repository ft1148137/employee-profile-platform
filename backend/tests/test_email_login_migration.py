from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, select

from app import crud
from app.core.config import settings
from app.core.db import init_db
from app.core.security import get_password_hash
from app.domains.org.models import Employee, Organization, OrgMembership
from app.models import User
from tests.utils.migrations import insert_historical_rows, migrate


def test_bootstrap_email_normalization(
    migration_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "FIRST_SUPERUSER", "Bootstrap@Example.com")
    monkeypatch.setattr(settings, "FIRST_SUPERUSER_PASSWORD", "synthetic-password")
    with Session(migration_engine) as session:
        init_db(session)
        init_db(session)
        users = session.exec(select(User)).all()
        assert len(users) == 1
        assert users[0].username == users[0].email == "bootstrap@example.com"


def test_email_login_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "f61a901bc001")
    original = {
        uuid4(): name
        for name in ("E01", "NO-EMAIL", "CONFLICT", "OWNER", "DUP1", "DUP2")
    }
    ids = dict(zip(original.values(), original, strict=True))
    with Session(migration_engine) as session:
        org = Organization(slug="email-migration", name="合成企业")
        insert_historical_rows(session, org)
        password_hash = get_password_hash("synthetic-password")
        for id, username in original.items():
            session.execute(
                text("""INSERT INTO "user"
                (id, username, email, hashed_password, is_active, is_superuser, must_change_password)
                VALUES (:id, :username, :email, :hash, true, false, false)"""),
                {
                    "id": id,
                    "username": username,
                    "email": "taken@example.com" if username == "OWNER" else None,
                    "hash": password_hash,
                },
            )
            employee = Employee(
                org_id=org.id,
                employee_code=username,
                display_name="合成员工",
                work_email={
                    "E01": "  Employee@Example.com  ",
                    "CONFLICT": "TAKEN@example.com",
                    "DUP1": "dup@example.com",
                    "DUP2": "DUP@example.com",
                }.get(username),
            )
            insert_historical_rows(session, employee)
            session.add(
                OrgMembership(org_id=org.id, user_id=id, employee_id=employee.id)
            )
        session.commit()
    migrate(migration_engine, "upgrade", "head")
    with Session(migration_engine) as session:
        for name, expected in (
            ("E01", "employee@example.com"),
            ("NO-EMAIL", "NO-EMAIL"),
            ("CONFLICT", "CONFLICT"),
            ("DUP1", "DUP1"),
            ("DUP2", "DUP2"),
        ):
            user = crud.authenticate(
                session=session,
                username=expected.upper(),
                password="synthetic-password",
            )
            assert (
                user is not None
                and user.id == ids[name]
                and user.legacy_username == name
            )
        assert (
            crud.authenticate(
                session=session, username="E01", password="synthetic-password"
            )
            is None
        )
    migrate(migration_engine, "downgrade", "f61a901bc001")
    with migration_engine.connect() as connection:
        restored = dict(
            connection.execute(text('SELECT id, username FROM "user"')).all()
        )
        assert restored == original
    assert "legacy_username" not in {
        c["name"] for c in inspect(migration_engine).get_columns("user")
    }
    migrate(migration_engine, "upgrade", "head")
