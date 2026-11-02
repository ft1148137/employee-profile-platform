from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep
from app.domains.setup import wizard
from app.domains.setup.models import SetupStatus
from app.domains.setup.schemas import SetupPublic

router = APIRouter(prefix="/orgs/{org_id}/setup", tags=["setup"])


@router.get("", response_model=SetupPublic)
def read_setup(session: SessionDep, principal: OrgAdminDep) -> SetupPublic:
    return wizard.read_setup(session, principal.org_id)


@router.post("/steps/{key}/complete", response_model=SetupPublic)
def complete_step(key: str, session: SessionDep, principal: OrgAdminDep) -> SetupPublic:
    return wizard.update_step(session, principal, key, SetupStatus.COMPLETED)


@router.post("/steps/{key}/skip", response_model=SetupPublic)
def skip_step(key: str, session: SessionDep, principal: OrgAdminDep) -> SetupPublic:
    return wizard.update_step(session, principal, key, SetupStatus.SKIPPED)
