from sqlalchemy import Engine, Uuid, inspect

from tests.utils.migrations import migrate


def test_im_borrow_assignment_migration(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "c840001bc001")
    assert "borrow_assignment_id" not in {
        column["name"]
        for column in inspect(migration_engine).get_columns("im_notification")
    }
    migrate(migration_engine, "upgrade", "head")
    assert any(
        column["name"] == "borrow_assignment_id"
        and isinstance(column["type"], Uuid)
        and column["nullable"]
        for column in inspect(migration_engine).get_columns("im_notification")
    )
