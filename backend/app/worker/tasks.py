from uuid import UUID

from procrastinate.exceptions import JobRetry
from procrastinate.job_context import JobContext
from procrastinate.jobs import Job
from procrastinate.retry import BaseRetryStrategy
from sqlmodel import Session

from app.core.db import engine
from app.domains.base import utc_now
from app.domains.bot.store import cleanup_messages
from app.domains.drafting.service import run_drafting
from app.domains.im.binding import cleanup_events
from app.domains.inbox.reminders import dispatch_due_reminders
from app.domains.source.schedule import dispatch_due_syncs
from app.domains.source.sync import execute_sync
from app.worker.app import app
from app.worker.tenant import tenant_task


class SyncRetry(BaseRetryStrategy):
    def get_retry_exception(
        self, exception: BaseException, job: Job
    ) -> JobRetry | None:
        return exception if isinstance(exception, JobRetry) else None


@app.task(name="system.ping")
async def ping() -> None:
    """Verify queue execution without accessing tenant data or external services."""


@app.periodic(cron="*/5 * * * *")
@app.task(name="bot.cleanup_messages", queueing_lock="bot_cleanup_messages")
def cleanup_bot_messages(timestamp: int) -> None:  # noqa: ARG001
    with Session(engine) as session:
        cleanup_messages(session, utc_now())
        cleanup_events(session)


@app.periodic(cron="*/5 * * * *")
@app.task(
    name="system.retry_stalled_jobs",
    queueing_lock="retry_stalled_jobs",
    pass_context=True,
)
async def retry_stalled_jobs(context: JobContext, timestamp: int) -> None:  # noqa: ARG001
    for job in await app.job_manager.get_stalled_jobs():
        await app.job_manager.retry_job(job)


@app.task(name="profiles.draft_initial")
def draft_initial(org_id: str, employee_ids: list[str] | None = None) -> None:
    tenant_task(UUID(org_id), lambda principal: run_drafting(principal, employee_ids))


@app.task(name="sources.sync_run", retry=SyncRetry())
def sync_run(org_id: str, run_id: str, initial_attempts: int = 0) -> None:
    tenant_task(
        UUID(org_id),
        lambda principal: execute_sync(principal, UUID(run_id), initial_attempts),
    )


@app.periodic(cron="0 * * * *")
@app.task(name="sources.dispatch_scheduled")
def dispatch_scheduled(timestamp: int) -> None:  # noqa: ARG001
    with Session(engine) as session:
        dispatch_due_syncs(session, utc_now())


@app.periodic(cron="0 * * * *")
@app.task(name="profiles.dispatch_reminders")
def dispatch_reminders(timestamp: int) -> None:  # noqa: ARG001
    with Session(engine) as session:
        dispatch_due_reminders(session, utc_now())
