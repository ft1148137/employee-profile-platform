from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep
from app.domains.source.identities import (
    link_identities,
    list_identities,
    unlink_identity,
)
from app.domains.source.schemas import (
    ExternalIdentitiesPublic,
    IdentityLinkResult,
    IdentityLinks,
    IdentityUnlinkResult,
)
from app.domains.work.ingest import IdentityNotFound

router = APIRouter(
    prefix="/orgs/{org_id}/external-identities", tags=["external-identities"]
)


@router.get("", response_model=ExternalIdentitiesPublic)
def get_identities(
    session: SessionDep,
    principal: OrgAdminDep,
    status: Literal["unlinked", "linked", "all"] = "unlinked",
    source: str | None = None,
    q: str | None = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ExternalIdentitiesPublic:
    return list_identities(
        session,
        principal.org_id,
        status=status,
        source=source,
        q=q,
        skip=skip,
        limit=limit,
    )


@router.post("/link", response_model=IdentityLinkResult)
def post_links(
    body: IdentityLinks, session: SessionDep, principal: OrgAdminDep
) -> IdentityLinkResult:
    try:
        return link_identities(session, principal, body)
    except IdentityNotFound:
        raise HTTPException(404, "账号不存在") from None


@router.post("/{identity_id}/unlink", response_model=IdentityUnlinkResult)
def post_unlink(
    identity_id: UUID, session: SessionDep, principal: OrgAdminDep
) -> IdentityUnlinkResult:
    try:
        return unlink_identity(session, principal, identity_id)
    except IdentityNotFound:
        raise HTTPException(404, "账号不存在") from None
