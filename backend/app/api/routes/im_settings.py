from fastapi import APIRouter, HTTPException

from app.api.deps import SessionDep
from app.api.secret_safe import SecretSafeRoute
from app.authz.deps import OrgAdminDep
from app.core.secrets import SecretUnavailable
from app.domains.im.schemas import (
    ImConnectionTest,
    ImSettingPublic,
    ImSettingUpdate,
    Platform,
)
from app.domains.im.settings import (
    InvalidImSetting,
    public_setting,
    save_setting,
    test_connection,
)

router = APIRouter(
    prefix="/orgs/{org_id}/im-settings",
    tags=["im-settings"],
    route_class=SecretSafeRoute,
)


@router.get("", response_model=list[ImSettingPublic])
def get_im_settings(db: SessionDep, principal: OrgAdminDep) -> list[ImSettingPublic]:
    return [
        public_setting(db, principal.org_id, platform)
        for platform in ("feishu", "wecom", "telegram")
    ]


@router.put("/{platform}", response_model=ImSettingPublic)
def put_im_setting(
    platform: Platform, body: ImSettingUpdate, db: SessionDep, principal: OrgAdminDep
) -> ImSettingPublic:
    try:
        return save_setting(db, principal, platform, body)
    except (InvalidImSetting, SecretUnavailable) as error:
        raise HTTPException(422, str(error)) from None


@router.post("/{platform}/test", response_model=ImConnectionTest)
def test_im_connection(
    platform: Platform, db: SessionDep, principal: OrgAdminDep
) -> ImConnectionTest:
    return test_connection(db, principal.org_id, platform)
