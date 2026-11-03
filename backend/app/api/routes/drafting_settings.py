from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep, OrgPrincipalDep
from app.domains.drafting.settings import (
    AutoDraftPublic,
    AutoDraftUpdate,
    DraftingSettingsPublic,
    DraftingSettingsUpdate,
    read_auto_draft,
    read_settings,
    save_auto_draft,
    save_settings,
)

router = APIRouter(prefix="/orgs/{org_id}", tags=["drafting-settings"])


@router.get("/drafting-settings", response_model=DraftingSettingsPublic)
def get_drafting_settings(
    session: SessionDep, principal: OrgAdminDep
) -> DraftingSettingsPublic:
    return read_settings(session, principal.org_id)


@router.put("/drafting-settings", response_model=DraftingSettingsPublic)
def put_drafting_settings(
    body: DraftingSettingsUpdate, session: SessionDep, principal: OrgAdminDep
) -> DraftingSettingsPublic:
    return save_settings(session, principal, body)


@router.get("/me/auto-draft", response_model=AutoDraftPublic)
def get_auto_draft(session: SessionDep, principal: OrgPrincipalDep) -> AutoDraftPublic:
    return read_auto_draft(session, principal)


@router.put("/me/auto-draft", response_model=AutoDraftPublic)
def put_auto_draft(
    body: AutoDraftUpdate, session: SessionDep, principal: OrgPrincipalDep
) -> AutoDraftPublic:
    return save_auto_draft(session, principal, body)
