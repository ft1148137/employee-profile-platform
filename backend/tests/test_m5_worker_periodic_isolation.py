from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from procrastinate import periodic
from sqlalchemy import text
from sqlmodel import Session, select

from app.domains.org.models import Department
from app.worker.app import app
from tests import test_worker


@pytest.mark.parametrize("worker", ["ping", "recovery"])
def test_workers_do_not_run_periodic_tasks_after_the_hour(
    db: Session, shared_demo: tuple, monkeypatch: pytest.MonkeyPatch, worker: str
) -> None:
    org_id = next(iter(shared_demo[1].values())).org_id
    statement = (
        select(Department.id, Department.capability_failed_at)
        .where(Department.org_id == org_id)
        .order_by(Department.id)
    )
    before = db.exec(statement).all()
    jobs_before = db.execute(text("SELECT id FROM procrastinate_jobs")).scalars().all()
    defers_before = db.execute(
        text("SELECT id FROM procrastinate_periodic_defers")
    ).all()
    now = datetime(2026, 11, 15, 1, 1, tzinfo=UTC).timestamp()
    monkeypatch.setattr(periodic, "time", SimpleNamespace(time=lambda: now))
    due = list(periodic.PeriodicDeferrer(app.periodic_registry).get_previous_tasks(now))
    assert "capabilities.fill_department_defaults" in {
        task.task.name for task, _ in due
    }

    if worker == "ping":
        test_worker.test_ping_job_succeeds(db, monkeypatch)
        expected_task = "system.ping"
    else:
        test_worker.test_recover_only_stalled_jobs(db, "missing", monkeypatch)
        expected_task = "system.retry_stalled_jobs"

    db.rollback()
    assert db.exec(statement).all() == before
    new_tasks = db.execute(text("SELECT id, task_name FROM procrastinate_jobs")).all()
    assert [name for job_id, name in new_tasks if job_id not in jobs_before] == [
        expected_task
    ]
    assert (
        db.execute(text("SELECT id FROM procrastinate_periodic_defers")).all()
        == defers_before
    )
