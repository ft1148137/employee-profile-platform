from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep
from app.domains.drafting.service import DraftProgress, queue_drafting, read_progress

router = APIRouter(prefix="/orgs/{org_id}/initial-profiles", tags=["initial-profiles"])


class DraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["all", "failed"]


class DraftQueued(BaseModel):
    queued: int


@router.post("/draft", status_code=202)
def draft(body: DraftBody, session: SessionDep, principal: OrgAdminDep) -> DraftQueued:
    return DraftQueued(queued=queue_drafting(session, principal.org_id, body.scope))


@router.get("/progress")
def progress(session: SessionDep, principal: OrgAdminDep) -> DraftProgress:
    return read_progress(session, principal.org_id)
