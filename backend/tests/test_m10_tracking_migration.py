from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect
from sqlmodel import Session, select

from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityAssignmentTask,
    CapacityDemand,
    CapacityProject,
)
from app.domains.org.models import Employee
from app.domains.work.models import Task
from tests.test_m10_project_migration import seed_m9
from tests.utils.migrations import insert_historical_rows, migrate

PREVIOUS = "c832001bc001"


@pytest.mark.parametrize("with_a1_data", [False, True])
def test_tracking_migration_roundtrip(
    migration_engine: Engine, with_a1_data: bool
) -> None:
    migrate(migration_engine, "downgrade", PREVIOUS)
    if with_a1_data:
        with Session(migration_engine) as db:
            demand, _, _ = seed_m9(db)
            project = CapacityProject(
                org_id=demand.org_id,
                requesting_department_id=demand.requesting_department_id,
                requester_user_id=demand.requester_user_id,
                title="合成项目",
                description="合成项目",
            )
            db.add(project)
            db.flush()
            saved = db.exec(select(CapacityDemand)).one()
            saved.project_id = project.id
            saved.role_name = "合成角色"
            worker = Employee(
                org_id=demand.org_id,
                department_id=demand.target_department_id,
                display_name="合成员工",
            )
            db.add(worker)
            db.flush()
            row = CapacityAssignment(
                org_id=demand.org_id,
                demand_id=demand.id,
                employee_id=worker.id,
                assigned_by_user_id=demand.requester_user_id,
                due_date=date(2026, 12, 1),
                allocation_percent=50,
            )
            insert_historical_rows(db, row)
            db.commit()

    for _ in range(2):
        migrate(migration_engine, "upgrade", "head")
        with Session(migration_engine) as db:
            assignments = db.exec(select(CapacityAssignment)).all()
            assert len(assignments) == int(with_a1_data)
            if with_a1_data:
                assert assignments[0].tracking_state is None
                assert assignments[0].tracking_changed_at is None
                assert db.exec(select(CapacityDemand)).one().role_name == "合成角色"
        migrate(migration_engine, "downgrade", PREVIOUS)
        assert (
            "capacity_assignment_task"
            not in inspect(migration_engine).get_table_names()
        )
    migrate(migration_engine, "upgrade", "head")
    if with_a1_data:
        latest_parent = "c839001bc001"
        migrate(migration_engine, "downgrade", latest_parent)
        migrate(migration_engine, "upgrade", "head")
        with Session(migration_engine) as db:
            assignment = db.exec(select(CapacityAssignment)).one()
            assignment.tracking_state = "status_unknown"
            db.commit()
        with pytest.raises(RuntimeError, match="存在关联任务状态待确认的分配"):
            migrate(migration_engine, "downgrade", latest_parent)
        with Session(migration_engine) as db:
            assignment = db.exec(select(CapacityAssignment)).one()
            assert assignment.tracking_state == "status_unknown"
            assignment.tracking_state = None
            db.commit()
        with Session(migration_engine) as db:
            assignment = db.exec(select(CapacityAssignment)).one()
            task = Task(
                org_id=assignment.org_id,
                source="jira",
                external_key=str(uuid4()),
                title="合成任务",
            )
            db.add(task)
            db.flush()
            db.add(
                CapacityAssignmentTask(
                    org_id=assignment.org_id,
                    assignment_id=assignment.id,
                    task_id=task.id,
                    linked_by_user_id=assignment.assigned_by_user_id,
                )
            )
            db.commit()
        with pytest.raises(RuntimeError, match="存在已关联的分配任务"):
            migrate(migration_engine, "downgrade", PREVIOUS)
        with Session(migration_engine) as db:
            assert (
                db.exec(select(CapacityAssignmentTask)).one().task_id
                == db.exec(select(Task)).one().id
            )
