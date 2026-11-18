import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.domains.planning.models import (
    OptionKind,
    PlanningCase,
    PlanningOption,
    PlanningSnapshot,
    PlanVerdict,
)
from tests.test_m10_project_migration import seed_m9
from tests.utils.migrations import migrate

PREVIOUS = "c837001bc001"


@pytest.mark.parametrize("with_old_option", [False, True])
def test_optimized_kind_roundtrip(
    migration_engine: Engine, with_old_option: bool
) -> None:
    migrate(migration_engine, "downgrade", PREVIOUS)
    if with_old_option:
        with Session(migration_engine) as session:
            demand, _, _ = seed_m9(session)
            case = PlanningCase(
                org_id=demand.org_id,
                demand_id=demand.id,
                created_by_user_id=demand.requester_user_id,
            )
            session.add(case)
            session.flush()
            snapshot = PlanningSnapshot(
                org_id=demand.org_id,
                case_id=case.id,
                data={},
                fingerprint="synthetic",
                created_by_user_id=demand.requester_user_id,
            )
            session.add(snapshot)
            session.flush()
            session.add(
                PlanningOption(
                    org_id=demand.org_id,
                    snapshot_id=snapshot.id,
                    kind=OptionKind.DIRECT,
                    verdict=PlanVerdict.FEASIBLE,
                    params={},
                    findings=[],
                    summary={},
                )
            )
            session.commit()

    for revision in ("head", PREVIOUS, "head"):
        migrate(
            migration_engine, "upgrade" if revision == "head" else "downgrade", revision
        )
        with Session(migration_engine) as session:
            assert session.exec(select(PlanningOption.kind)).all() == (
                ["direct"] if with_old_option else []
            )

    if with_old_option:
        with Session(migration_engine) as session:
            option = session.exec(select(PlanningOption)).one()
            option.kind = OptionKind.OPTIMIZED
            session.commit()
        with pytest.raises(RuntimeError, match="存在优化方案"):
            migrate(migration_engine, "downgrade", PREVIOUS)
        with Session(migration_engine) as session:
            option = session.exec(select(PlanningOption)).one()
            assert option.kind == "optimized"
            option.kind = OptionKind.DIRECT
            session.commit()
        migrate(migration_engine, "downgrade", PREVIOUS)
        with Session(migration_engine) as session:
            option = session.exec(select(PlanningOption)).one()
            option.kind = OptionKind.OPTIMIZED
            with pytest.raises(IntegrityError, match="planning_option_kind_check"):
                session.commit()
        migrate(migration_engine, "upgrade", "head")
