from fastapi import APIRouter, Response

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.im import binding
from app.domains.im.schemas import BindingCode, BindingPublic, Platform

router = APIRouter(prefix="/orgs/{org_id}/me", tags=["im-bindings"])


@router.post("/im-bind-code", response_model=BindingCode)
def create_im_bind_code(
    db: SessionDep, principal: OrgPrincipalDep, response: Response
) -> BindingCode:
    response.headers["Cache-Control"] = "no-store"
    return binding.create_code(db, principal)


@router.get("/im-bindings", response_model=list[BindingPublic])
def read_im_bindings(db: SessionDep, principal: OrgPrincipalDep) -> list[BindingPublic]:
    return binding.list_bindings(db, principal)


@router.delete("/im-bindings/{platform}", status_code=204)
def delete_im_binding(
    platform: Platform, db: SessionDep, principal: OrgPrincipalDep
) -> None:
    binding.unbind(db, principal, platform)
