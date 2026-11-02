from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import AwareDatetime

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep
from app.domains.audit.schemas import AuditEventsPublic
from app.domains.audit.service import list_events

router = APIRouter(prefix="/orgs/{org_id}/audit-events", tags=["audit"])


@router.get("", response_model=AuditEventsPublic)
def read_audit_events(
    session: SessionDep,
    principal: OrgAdminDep,
    action: str | None = None,
    actor_user_id: UUID | None = None,
    since: AwareDatetime | None = None,
    until: AwareDatetime | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> AuditEventsPublic:
    return list_events(
        session, principal.org_id, action, actor_user_id, since, until, skip, limit
    )
