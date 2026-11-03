from uuid import UUID

from procrastinate.exceptions import JobRetry
from procrastinate.jobs import Job
from procrastinate.retry import BaseRetryStrategy

from app.domains.drafting.service import run_drafting
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


@app.task(name="profiles.draft_initial")
def draft_initial(org_id: str, employee_ids: list[str] | None = None) -> None:
    tenant_task(UUID(org_id), lambda principal: run_drafting(principal, employee_ids))


@app.task(name="sources.sync_run", retry=SyncRetry())
def sync_run(org_id: str, run_id: str, initial_attempts: int = 0) -> None:
    tenant_task(
        UUID(org_id),
        lambda principal: execute_sync(principal, UUID(run_id), initial_attempts),
    )
