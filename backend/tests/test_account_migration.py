import subprocess
import sys
from pathlib import Path

from sqlalchemy import Engine, text
from sqlmodel import Session

from app.core.config import settings
from app.core.db import init_db
from app.seed.loader import load_demo


def migrate(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args], check=True, capture_output=True
    )


def test_account_migration_roundtrip(migration_engine: Engine) -> None:
    with Session(migration_engine) as session:
        init_db(session)
        session.rollback()
        load_demo(session, Path(settings.DEMO_DATA_DIR))
    migrate("downgrade", "7d3e0f1a5b92")
    migrate("upgrade", "head")
    with migration_engine.begin() as connection:
        users = dict(
            connection.execute(text('SELECT email, username FROM "user"')).all()
        )
        assert users["manager.platform@demo.example.com"] == "E01"
        assert users["employee.platform@demo.example.com"] == "E02"
        assert users["employee.algorithm@demo.example.com"] == "E05"
        assert users["admin@demo.example.com"] == "admin@demo.example.com"
        connection.execute(
            text('UPDATE "user" SET email = NULL WHERE username = :username'),
            {"username": "E02"},
        )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "7d3e0f1a5b92"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "fill every email" in result.stderr
    with migration_engine.begin() as connection:
        connection.execute(
            text('UPDATE "user" SET email = :email WHERE username = :username'),
            {"email": "employee.platform@demo.example.com", "username": "E02"},
        )
    migrate("downgrade", "7d3e0f1a5b92")
    migrate("upgrade", "head")
