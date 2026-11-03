from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import init_db
from app.domains.org.models import Department, Organization
from app.seed.loader import load_demo
from tests.utils.migrations import migrate

PREVIOUS = "d73e295a94f6"


def test_roster_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", PREVIOUS)
    # The new column must backfill an organization that predates this migration.
    with migration_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization (id, created_at, slug, name) VALUES (gen_random_uuid(), now(), 'before-roster', 'Synthetic')"
            )
        )
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT roster_revision FROM organization WHERE slug = 'before-roster'"
                )
            ).scalar_one()
            == 0
        )
    with Session(migration_engine) as session:
        init_db(session)
        session.rollback()
        load_demo(session, Path(settings.DEMO_DATA_DIR))
    migrate(migration_engine, "downgrade", PREVIOUS)
    migrate(migration_engine, "upgrade", "head")
    with Session(migration_engine) as session:
        org = session.exec(
            select(Organization).where(Organization.slug == "demo")
        ).one()
        root = session.exec(
            select(Department).where(Department.org_id == org.id)
        ).first()
        session.add(
            Department(org_id=org.id, name=root.name, parent_department_id=root.id)
        )
        session.commit()
        # Seed lookups must keep choosing the root when a child has the same name.
        load_demo(session, Path(settings.DEMO_DATA_DIR))
    with pytest.raises(ValueError, match="duplicate names"):
        migrate(migration_engine, "downgrade", PREVIOUS)
    with migration_engine.begin() as connection:
        assert (
            connection.execute(
                text("SELECT to_regclass('reporting_line')")
            ).scalar_one()
            == "reporting_line"
        )
        connection.execute(
            text("DELETE FROM department WHERE parent_department_id IS NOT NULL")
        )
    migrate(migration_engine, "downgrade", PREVIOUS)
    migrate(migration_engine, "upgrade", "head")
    migrate(migration_engine, "upgrade", "heads")
