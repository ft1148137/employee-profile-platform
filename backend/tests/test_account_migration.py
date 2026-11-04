from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlmodel import Session

from app.core.config import settings
from app.core.db import init_db
from app.seed.loader import load_demo
from tests.utils.migrations import migrate


def test_account_migration_roundtrip(migration_engine: Engine) -> None:
    with Session(migration_engine) as session:
        init_db(session)
        session.rollback()
        load_demo(session, Path(settings.DEMO_DATA_DIR))
    migrate(migration_engine, "downgrade", "7d3e0f1a5b92")
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.begin() as connection:
        users = dict(
            connection.execute(text('SELECT email, username FROM "user"')).all()
        )
        assert users["employee01@demo.example.com"] == "employee01@demo.example.com"
        assert users["employee02@demo.example.com"] == "employee02@demo.example.com"
        assert users["employee05@demo.example.com"] == "employee05@demo.example.com"
        assert users["admin@demo.example.com"] == "admin@demo.example.com"
        connection.execute(
            text('UPDATE "user" SET email = NULL WHERE username = :username'),
            {"username": "employee02@demo.example.com"},
        )
    with pytest.raises(ValueError, match="fill every email"):
        migrate(migration_engine, "downgrade", "7d3e0f1a5b92")
    with migration_engine.begin() as connection:
        connection.execute(
            text('UPDATE "user" SET email = :email WHERE username = :username'),
            {
                "email": "employee02@demo.example.com",
                "username": "employee02@demo.example.com",
            },
        )
    migrate(migration_engine, "downgrade", "7d3e0f1a5b92")
    migrate(migration_engine, "upgrade", "head")
