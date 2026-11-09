from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep
from app.domains.admin.overview import AdminOverviewPublic, build_overview

router = APIRouter(prefix="/orgs/{org_id}/admin", tags=["admin"])


@router.get("/overview")
def get_overview(session: SessionDep, principal: OrgAdminDep) -> AdminOverviewPublic:
    return build_overview(session, principal)
