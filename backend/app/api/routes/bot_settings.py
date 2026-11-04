from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep
from app.domains.bot.settings import BotSettings, read_settings, save_settings

router = APIRouter(prefix="/orgs/{org_id}/bot-settings", tags=["bot-settings"])


@router.get("", response_model=BotSettings)
def get_bot_settings(db: SessionDep, principal: OrgAdminDep) -> BotSettings:
    return read_settings(db, principal.org_id)


@router.put("", response_model=BotSettings)
def put_bot_settings(
    body: BotSettings, db: SessionDep, principal: OrgAdminDep
) -> BotSettings:
    return save_settings(db, principal, body)
