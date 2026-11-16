import sqlalchemy as sa
from sqlalchemy import Engine
from sqlmodel import SQLModel

from tests.utils.migrations import migrate


def test_planning_migration_roundtrip(migration_engine: Engine) -> None:
    assert {"planning_case", "planning_snapshot", "planning_option"} <= set(
        SQLModel.metadata.tables
    )
    migrate(migration_engine, "downgrade", "c693001bc001")
    assert "planning_case" not in sa.inspect(migration_engine).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    assert {"planning_case", "planning_snapshot", "planning_option"} <= set(
        sa.inspect(migration_engine).get_table_names()
    )
    migrate(migration_engine, "downgrade", "c693001bc001")
    assert "planning_case" not in sa.inspect(migration_engine).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    assert "planning_option" in sa.inspect(migration_engine).get_table_names()
