from uuid import UUID

from app.domains.drafting.service import run_drafting
from app.worker.app import app
from app.worker.tenant import tenant_task


@app.task(name="system.ping")
async def ping() -> None:
    """Verify queue execution without accessing tenant data or external services."""


@app.task(name="profiles.draft_initial")
def draft_initial(org_id: str, employee_ids: list[str] | None = None) -> None:
    tenant_task(UUID(org_id), lambda principal: run_drafting(principal, employee_ids))
