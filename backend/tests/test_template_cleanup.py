import subprocess
import sys

from sqlalchemy import inspect
from sqlmodel import Session

from app.core.db import engine
from app.main import app


def test_template_cleanup_migration(db: Session) -> None:
    # Release earlier tests' read locks before running schema changes.
    db.rollback()
    assert "item" not in inspect(engine).get_table_names()
    assert not any("/items" in path for path in app.openapi()["paths"])

    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "fe56fa70289e"], check=True
    )
    try:
        inspector = inspect(engine)
        columns = {column["name"]: column for column in inspector.get_columns("item")}
        assert set(columns) == {"id", "title", "description", "owner_id", "created_at"}
        assert columns["title"]["type"].length == 255
        assert not columns["title"]["nullable"]
        assert columns["description"]["nullable"]
        assert not columns["owner_id"]["nullable"]
        assert columns["created_at"]["type"].timezone
        foreign_key = inspector.get_foreign_keys("item")[0]
        assert foreign_key["referred_table"] == "user"
        assert foreign_key["options"]["ondelete"] == "CASCADE"
    finally:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)

    assert "item" not in inspect(engine).get_table_names()
