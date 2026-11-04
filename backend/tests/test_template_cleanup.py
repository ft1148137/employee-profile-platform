from sqlalchemy import Engine, inspect
from sqlmodel import Session

from app.core.config import settings
from app.main import app
from tests.utils.migrations import migrate


def test_template_cleanup_migration(db: Session, migration_engine: Engine) -> None:
    engine = migration_engine
    # Release earlier tests' read locks before running schema changes.
    db.rollback()
    assert "item" not in inspect(engine).get_table_names()
    paths = app.openapi()["paths"]
    legacy = f"{settings.API_V1_STR}/items"
    assert not any(path == legacy or path.startswith(f"{legacy}/") for path in paths)
    assert not any(
        "items" in operation.get("tags", [])
        for methods in paths.values()
        for operation in methods.values()
    )
    assert (
        "put"
        in paths[
            f"{settings.API_V1_STR}/orgs/{{org_id}}/me/requests/{{request_id}}/items"
        ]
    )

    migrate(migration_engine, "downgrade", "fe56fa70289e")
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
        migrate(migration_engine, "upgrade", "head")

    assert "item" not in inspect(engine).get_table_names()
