import asyncio

from sqlalchemy import Engine, inspect, text
from sqlmodel import Session

from app.core.db import engine
from app.worker.app import app
from app.worker.tasks import ping
from tests.utils.migrations import migrate


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
