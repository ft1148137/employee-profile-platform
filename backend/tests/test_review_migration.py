from sqlalchemy import Engine, inspect, text

from tests.utils.migrations import migrate


def test_review_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b119001bc001")
    with migration_engine.connect() as connection:
        inspector = inspect(connection)
        assert "review_decision" not in inspector.get_table_names()
        assert "decision" not in {
            c["name"] for c in inspector.get_columns("change_item")
        }
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        inspector = inspect(connection)
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).all() == [("b117001bc001",)]
        assert {
            tuple(fk["constrained_columns"])
            for fk in inspector.get_foreign_keys("review_decision")
        } >= {
            ("org_id", "request_id"),
            ("org_id", "reviewer_membership_id"),
            ("org_id", "acting_for_membership_id"),
        }
        assert {
            tuple(fk["constrained_columns"])
            for fk in inspector.get_foreign_keys("change_item")
        } >= {
            ("org_id", "review_decision_id"),
            ("org_id", "published_claim_id"),
        }
