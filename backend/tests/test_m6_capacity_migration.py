from sqlalchemy import Engine, inspect

from tests.utils.migrations import migrate

PREVIOUS = "b622001dc001"
TABLES = {
    "capacity_setting",
    "capacity_workload",
    "capacity_workload_request",
    "capacity_demand",
    "capacity_demand_event",
    "capacity_assignment",
    "capacity_extension_request",
}


def test_capacity_roundtrip(migration_engine: Engine) -> None:
    for action, revision in [
        ("downgrade", PREVIOUS),
        ("upgrade", "c641001bc001"),
        ("downgrade", PREVIOUS),
        ("upgrade", "c641001bc001"),
    ]:
        migrate(migration_engine, action, revision)
        inspector = inspect(migration_engine)
        assert TABLES.intersection(inspector.get_table_names()) == (
            TABLES if action == "upgrade" else set()
        )
        constraint = next(
            c
            for c in inspector.get_check_constraints("inbox_item")
            if c["name"] == "inbox_item_kind_check"
        )
        assert ("'capacity'" in constraint["sqltext"]) == (action == "upgrade")
        if action == "upgrade":
            for table in TABLES:
                assert "org_id" in {c["name"] for c in inspector.get_columns(table)}
                assert all(
                    "org_id" in fk["constrained_columns"]
                    for fk in inspector.get_foreign_keys(table)
                )
            for table, index in [
                ("capacity_workload_request", "uq_capacity_workload_pending"),
                ("capacity_assignment", "uq_capacity_assignment_active"),
                ("capacity_extension_request", "uq_capacity_extension_pending"),
            ]:
                assert any(
                    i["name"] == index
                    and i["unique"]
                    and i["dialect_options"]["postgresql_where"]
                    for i in inspector.get_indexes(table)
                )
