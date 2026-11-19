import logging
from uuid import UUID

from procrastinate.exceptions import JobRetry
from procrastinate.job_context import JobContext
from procrastinate.jobs import Job
from procrastinate.retry import BaseRetryStrategy
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.core.db import engine
from app.domains.base import utc_now
from app.domains.bot.store import cleanup_messages
from app.domains.capability.department_defaults import fill_department_defaults
from app.domains.capacity.proposal import expire
from app.domains.capacity.tracking import recompute
from app.domains.drafting.incremental import run_incremental
from app.domains.drafting.service import run_drafting
from app.domains.im.binding import cleanup_events
from app.domains.inbox.admin_todos import dispatch_admin_todos
from app.domains.inbox.reminders import dispatch_due_reminders
from app.domains.org.models import Organization
from app.domains.source.schedule import dispatch_due_syncs
from app.domains.source.sync import execute_sync
from app.worker.app import app
from app.worker.health import write_heartbeat
from app.worker.tenant import tenant_task

logger = logging.getLogger(__name__)


class SyncRetry(BaseRetryStrategy):
    def get_retry_exception(
        self, exception: BaseException, job: Job
    ) -> JobRetry | None:
        return exception if isinstance(exception, JobRetry) else None


@app.periodic(cron="* * * * *")
@app.task(name="system.heartbeat", queueing_lock="system_heartbeat")
def heartbeat(timestamp: int) -> None:  # noqa: ARG001
    write_heartbeat(engine)


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
@app.task(name="capacity.expire_proposals", queueing_lock="capacity_expire_proposals")
def expire_proposals(timestamp: int) -> None:  # noqa: ARG001
    with Session(engine) as session:
        org_ids = session.exec(
            select(Organization.id).order_by(col(Organization.id))
        ).all()
    for org_id in org_ids:
        with Session(engine) as session:
            try:
                expire(Principal.system_worker(org_id), session)
                session.commit()
            except Exception as exc:
                session.rollback()
                logger.error(
                    "Proposal expiration failed for org %s (%s)",
                    org_id,
                    type(exc).__name__,
                )


@app.periodic(cron="0 0 * * *")
@app.task(name="capacity.refresh_tracking", queueing_lock="capacity_refresh_tracking")
def refresh_tracking(timestamp: int) -> None:  # noqa: ARG001
    with Session(engine) as session:
        org_ids = session.exec(select(Organization.id)).all()
    for org_id in org_ids:
        try:
            with Session(engine) as session:
                recompute(session, org_id)
                session.commit()
        except Exception as exc:
            logger.warning(
                "Daily tracking refresh failed for org %s (%s)",
                org_id,
                type(exc).__name__,
            )


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
        lambda principal: execute_sync(
            principal, UUID(run_id), initial_attempts, after_ingest=recompute
        ),
    )


@app.task(name="profiles.draft_incremental")
def draft_incremental(org_id: str, employee_ids: list[str] | None = None) -> None:
    tenant_task(
        UUID(org_id), lambda principal: run_incremental(principal, employee_ids)
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


@app.periodic(cron="0 * * * *")
@app.task(
    name="capabilities.fill_department_defaults", queueing_lock="department_defaults"
)
def fill_defaults(timestamp: int) -> None:  # noqa: ARG001
    with Session(engine) as session:
        org_ids = session.exec(select(Organization.id)).all()
    for org_id in org_ids:
        tenant_task(org_id, fill_department_defaults)


@app.periodic(cron="0 * * * *")
@app.task(name="admin.dispatch_todos", queueing_lock="admin_dispatch_todos")
def dispatch_admin_digest(timestamp: int) -> None:
    from datetime import UTC, datetime

    with Session(engine) as session:
        dispatch_admin_todos(session, datetime.fromtimestamp(timestamp, UTC))
