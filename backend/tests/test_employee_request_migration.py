from pathlib import Path

from sqlalchemy import Engine, inspect, text
from sqlmodel import Session

from app.core.config import settings
from app.core.db import init_db
from app.seed.loader import load_demo
from tests.utils.migrations import migrate

PREVIOUS = "a87e001bc001"


def test_employee_request_roundtrip(migration_engine: Engine) -> None:
    with Session(migration_engine) as session:
        init_db(session)
        session.rollback()
        load_demo(session, Path(settings.DEMO_DATA_DIR))
    migrate(migration_engine, "downgrade", PREVIOUS)
    with migration_engine.connect() as connection:
        original = connection.execute(
            text("SELECT id,origin,status FROM profile_change_request ORDER BY id")
        ).all()
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        assert connection.execute(
            text("SELECT DISTINCT revision FROM profile_change_request")
        ).scalars().all() == [1]
        assert (
            connection.execute(
                text("SELECT id,origin,status FROM profile_change_request ORDER BY id")
            ).all()
            == original
        )
        assert "inbox_item" in inspect(connection).get_table_names()
        assert (
            connection.execute(text("SELECT count(*) FROM inbox_item")).scalar_one()
            == 0
        )
    migrate(migration_engine, "downgrade", PREVIOUS)
    with migration_engine.connect() as connection:
        assert "inbox_item" not in inspect(connection).get_table_names()
        assert "revision" not in {
            column["name"]
            for column in inspect(connection).get_columns("profile_change_request")
        }
        assert (
            connection.execute(
                text("SELECT id,origin,status FROM profile_change_request ORDER BY id")
            ).all()
            == original
        )
    migrate(migration_engine, "upgrade", "head")
