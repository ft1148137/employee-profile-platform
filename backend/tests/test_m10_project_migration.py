from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.domains.base import utc_now
from app.domains.capacity.models import (
    CapacityApproval,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityProject,
    CapacityProposal,
    DemandEventKind,
)
from app.domains.org.models import Department, Organization, OrgMembership
from app.models import User
from tests.utils.migrations import insert_historical_rows, migrate

PREVIOUS = "c782001bc001"


def seed_m9(
    session: Session,
) -> tuple[CapacityDemand, CapacityDemandEvent, CapacityApproval]:
    org = Organization(slug=str(uuid4()), name="合成企业")
    user = User(username=f"{uuid4()}@example.com", hashed_password="unused")
    first = Department(org_id=org.id, name="需求部门")
    second = Department(org_id=org.id, name="承接部门")
    session.add_all([org, user])
    session.flush()
    session.add_all([first, second, OrgMembership(org_id=org.id, user_id=user.id)])
    session.flush()
    demand = CapacityDemand(
        org_id=org.id,
        requesting_department_id=first.id,
        requester_user_id=user.id,
        target_department_id=second.id,
        capability_key="delivery",
        capability_name="交付",
        headcount=1,
        allocation_percent=50,
        window_start=date(2026, 12, 1),
        window_end=date(2026, 12, 31),
        description="合成需求",
    )
    proposal = CapacityProposal(
        org_id=org.id,
        demand_id=demand.id,
        source_option_id=uuid4(),
        demand_revision=1,
        proposed_by_user_id=user.id,
        hold_expires_at=utc_now() + timedelta(hours=72),
    )
    event = CapacityDemandEvent(
        org_id=org.id,
        demand_id=demand.id,
        actor_user_id=user.id,
        kind=DemandEventKind.SUBMITTED,
        text="合成事件",
        via="web",
    )
    approval = CapacityApproval(
        org_id=org.id,
        proposal_id=proposal.id,
        user_id=user.id,
        party="resource_manager",
        decision="approved",
        via="web",
    )
    insert_historical_rows(session, demand, event, proposal, approval)
    return demand, event, approval


@pytest.mark.parametrize("with_m9_data", [False, True])
def test_m10_migration_roundtrip(migration_engine: Engine, with_m9_data: bool) -> None:
    migrate(migration_engine, "downgrade", PREVIOUS)
    if with_m9_data:
        with Session(migration_engine) as session:
            seed_m9(session)
            session.commit()
    for _ in range(2):
        migrate(migration_engine, "upgrade", "head")
        with Session(migration_engine) as session:
            rows = session.exec(select(CapacityDemand)).all()
            assert len(rows) == int(with_m9_data)
            if with_m9_data:
                assert rows[0].extra_capabilities == []
                assert rows[0].project_id is None
                assert session.exec(select(CapacityApproval)).one().reason_code is None
                assert (
                    session.exec(select(CapacityDemandEvent)).one().reason_code is None
                )
        migrate(migration_engine, "downgrade", PREVIOUS)
        assert "capacity_project" not in inspect(migration_engine).get_table_names()
    migrate(migration_engine, "upgrade", "head")


@pytest.mark.parametrize("blocker", ["project", "approval", "event"])
def test_m10_downgrade_preserves_new_data(
    migration_engine: Engine, blocker: str
) -> None:
    with Session(migration_engine) as session:
        legacy, event, approval = seed_m9(session)
        session.commit()
        demand = session.exec(select(CapacityDemand)).one()
        assert demand.id == legacy.id
        if blocker == "project":
            project = CapacityProject(
                org_id=demand.org_id,
                requesting_department_id=demand.requesting_department_id,
                requester_user_id=demand.requester_user_id,
                title="合成项目",
                description="",
            )
            session.add(project)
            session.flush()
            with pytest.raises(IntegrityError), session.begin_nested():
                demand.project_id = project.id
                session.flush()
            demand.project_id = project.id
            demand.role_name = "开发"
        else:
            reason_row = (
                session.get_one(CapacityApproval, approval.id)
                if blocker == "approval"
                else session.get_one(CapacityDemandEvent, event.id)
            )
            with pytest.raises(IntegrityError), session.begin_nested():
                reason_row.reason_code = "invalid"
                session.flush()
            reason_row.reason_code = "capacity"
        session.commit()

    with pytest.raises(RuntimeError, match="无法降级"):
        migrate(migration_engine, "downgrade", PREVIOUS)
    with Session(migration_engine) as session:
        assert session.exec(select(CapacityDemand)).one().id == legacy.id
        if blocker == "project":
            assert session.exec(select(CapacityProject)).one().title == "合成项目"
        elif blocker == "approval":
            assert (
                session.get_one(CapacityApproval, approval.id).reason_code == "capacity"
            )
        else:
            assert (
                session.get_one(CapacityDemandEvent, event.id).reason_code == "capacity"
            )


def test_m10_downgrade_waits_for_in_flight_reason(migration_engine: Engine) -> None:
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import text

    with Session(migration_engine) as session:
        _, event, _ = seed_m9(session)
        session.commit()
    inserted, release = Event(), Event()

    def write_reason() -> None:
        with Session(migration_engine) as session:
            row = session.get_one(CapacityDemandEvent, event.id)
            row.reason_code = "timing"
            session.flush()
            inserted.set()
            assert release.wait(timeout=10)
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(write_reason)
        assert inserted.wait(timeout=10)
        downgrade = pool.submit(migrate, migration_engine, "downgrade", PREVIOUS)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with migration_engine.connect() as connection:
                    waiting = connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                            "AND query LIKE 'LOCK TABLE capacity_approval,%')"
                        )
                    )
                if waiting:
                    break
                time.sleep(0.02)
            else:
                pytest.fail("Downgrade did not wait for the in-flight reason write")
        finally:
            release.set()
        writer.result(timeout=10)
        with pytest.raises(RuntimeError, match="无法降级"):
            downgrade.result(timeout=10)

    with Session(migration_engine) as session:
        assert session.get_one(CapacityDemandEvent, event.id).reason_code == "timing"
