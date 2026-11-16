from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.domains.capacity.models import CapacityWeek
from app.domains.org.models import Employee, Organization, OrgMembership
from app.models import User
from tests.utils.migrations import migrate


def test_weekly_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "c641001bc001")
    org = Organization(slug="weekly-migration", name="合成企业")
    user = User(username=f"{uuid4()}@example.com", hashed_password="unused-synthetic")
    person = Employee(org_id=org.id, display_name="合成员工")
    confirmed = datetime(2026, 11, 15, 16, 30, tzinfo=UTC)
    with Session(migration_engine) as session:
        session.add_all([org, user])
        session.flush()
        session.add_all([person, OrgMembership(org_id=org.id, user_id=user.id)])
        session.flush()
        table = sa.Table(
            "capacity_workload", sa.MetaData(), autoload_with=session.connection()
        )
        session.execute(
            table.insert().values(
                id=uuid4(),
                created_at=confirmed,
                org_id=org.id,
                employee_id=person.id,
                base_percent=150,
                updated_by_user_id=user.id,
                updated_at=confirmed,
            )
        )
        session.commit()
        org_id, employee_id, user_id = org.id, person.id, user.id
    migrate(migration_engine, "upgrade", "head")
    assert "capacity_workload" not in sa.inspect(migration_engine).get_table_names()
    with Session(migration_engine) as session:
        row = session.exec(
            select(CapacityWeek).where(CapacityWeek.org_id == org_id)
        ).one()
        assert (
            row.week_start,
            row.other_percent,
            row.confirmed_at,
            row.confirmed_by_user_id,
        ) == (date(2026, 11, 9), 150, confirmed, user_id)
        session.add(
            CapacityWeek(
                org_id=org_id,
                employee_id=employee_id,
                week_start=date(2026, 11, 16),
                other_percent=60,
                confirmed_by_user_id=user_id,
                confirmed_at=confirmed - timedelta(days=1),
            )
        )
        session.commit()
    migrate(migration_engine, "downgrade", "c641001bc001")
    with migration_engine.connect() as connection:
        table = sa.Table("capacity_workload", sa.MetaData(), autoload_with=connection)
        row = (
            connection.execute(sa.select(table).where(table.c.org_id == org_id))
            .mappings()
            .one()
        )
        assert row["base_percent"] == 60
        assert row["updated_at"] == confirmed - timedelta(days=1)
        assert row["updated_by_user_id"] == user_id
    migrate(migration_engine, "upgrade", "head")
    with Session(migration_engine) as session:
        row = session.exec(
            select(CapacityWeek).where(CapacityWeek.org_id == org_id)
        ).one()
        assert row.other_percent == 60
