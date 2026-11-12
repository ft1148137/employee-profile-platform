import asyncio

import pytest
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session

from app.core.db import engine
from app.worker.app import app
from app.worker.tasks import ping, retry_stalled_jobs
from tests.utils.migrations import migrate


def recover_stalled_jobs() -> None:
    async def run() -> None:
        async with app.open_async():
            # Keep manual recovery independent of the cron job queued by workers.
            await retry_stalled_jobs.configure(
                queue="recovery", queueing_lock="test_recovery"
            ).defer_async(timestamp=0)
            await asyncio.wait_for(
                app.run_worker_async(wait=False, queues=["recovery"]), timeout=20
            )

    asyncio.run(run())


@pytest.mark.parametrize("worker", ["missing", "healthy", "stalled"])
def test_recover_only_stalled_jobs(db: Session, worker: str) -> None:
    job_id = ping.configure(
        connection=db.connection().connection.driver_connection
    ).defer()
    worker_id = None
    if worker != "missing":
        worker_id = db.execute(
            text(
                "INSERT INTO procrastinate_workers (last_heartbeat) VALUES "
                "(now() - :age * interval '1 minute') RETURNING id"
            ),
            {"age": int(worker == "stalled")},
        ).scalar_one()
    db.execute(
        text(
            "UPDATE procrastinate_jobs SET status = 'doing', worker_id = :worker WHERE id = :id"
        ),
        {"worker": worker_id, "id": job_id},
    )
    db.commit()
    try:
        recover_stalled_jobs()
        assert db.execute(
            text("SELECT status FROM procrastinate_jobs WHERE id = :id"),
            {"id": job_id},
        ).scalar_one() == ("doing" if worker == "healthy" else "todo")
    finally:
        db.execute(
            text("DELETE FROM procrastinate_jobs WHERE id = :id"), {"id": job_id}
        )
        db.execute(
            text("DELETE FROM procrastinate_workers WHERE id = :id"), {"id": worker_id}
        )
        db.commit()


def test_stalled_recovery_is_periodic() -> None:
    periodic = app.periodic_registry.periodic_tasks[("system.retry_stalled_jobs", "")]
    assert periodic.cron == "*/5 * * * *"
    assert periodic.task.queueing_lock == "retry_stalled_jobs"


def test_department_defaults_are_hourly_and_locked() -> None:
    periodic = app.periodic_registry.periodic_tasks[
        ("capabilities.fill_department_defaults", "")
    ]
    assert periodic.cron == "0 * * * *"
    assert periodic.task.queueing_lock == "department_defaults"


def test_ping_job_succeeds(db: Session) -> None:
    db.rollback()

    async def run() -> int:
        async with app.open_async():
            job_id = await ping.defer_async()
            await asyncio.wait_for(app.run_worker_async(wait=False), timeout=20)
            return job_id

    job_id = asyncio.run(run())
    with Session(engine) as session:
        status = session.execute(
            text("SELECT status FROM procrastinate_jobs WHERE id = :job_id"),
            {"job_id": job_id},
        ).scalar_one()
        assert status == "succeeded"


def test_worker_migration_roundtrip(db: Session, migration_engine: Engine) -> None:
    engine = migration_engine
    db.rollback()
    migrate(migration_engine, "downgrade", "84c2e7d91a60")
    try:
        assert {"user", "organization", "evidence"} <= set(
            inspect(engine).get_table_names()
        )
        assert not any(
            name.startswith("procrastinate_")
            for name in inspect(engine).get_table_names()
        )
        with Session(engine) as session:
            assert (
                session.execute(
                    text(
                        "SELECT count(*) FROM pg_proc WHERE proname LIKE 'procrastinate_%'"
                    )
                ).scalar_one()
                == 0
            )
            assert (
                session.execute(
                    text(
                        "SELECT count(*) FROM pg_type WHERE typname LIKE 'procrastinate_%'"
                    )
                ).scalar_one()
                == 0
            )
    finally:
        migrate(migration_engine, "upgrade", "head")
    assert "procrastinate_jobs" in inspect(engine).get_table_names()
