from uuid import UUID

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.profile import service
from app.domains.profile.schemas import (
    ProfileVersionsPublic,
    ProfileVersionView,
    ProfileView,
)

router = APIRouter(prefix="/orgs/{org_id}/employees/{employee_id}", tags=["profiles"])


@router.get("/profile", response_model=ProfileView)
def read_profile(
    employee_id: UUID, session: SessionDep, principal: OrgPrincipalDep
) -> ProfileView:
    return service.read_profile(session, principal, employee_id)


@router.get("/profile-versions", response_model=ProfileVersionsPublic)
def read_profile_versions(
    employee_id: UUID, session: SessionDep, principal: OrgPrincipalDep
) -> ProfileVersionsPublic:
    return service.list_versions(session, principal, employee_id)


@router.get("/profile-versions/{revision}", response_model=ProfileVersionView)
def read_profile_version(
    employee_id: UUID, revision: int, session: SessionDep, principal: OrgPrincipalDep
) -> ProfileVersionView:
    return service.read_version(session, principal, employee_id, revision)
