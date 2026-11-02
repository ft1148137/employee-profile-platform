from collections.abc import Callable
from uuid import UUID

from sqlmodel import Session

from app.authz.principal import Principal
from app.core.db import engine
from app.domains.org.models import Organization


class TenantNotFound(ValueError):
    pass


def tenant_task[T](org_id: UUID, task: Callable[[Principal], T]) -> T:
    """Validate a job's explicit tenant before invoking its synchronous body."""
    with Session(engine) as session:
        if session.get(Organization, org_id) is None:
            raise TenantNotFound("Organization not found")
    return task(Principal.system_worker(org_id))
