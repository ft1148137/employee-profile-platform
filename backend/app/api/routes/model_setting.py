from fastapi import APIRouter, HTTPException

from app.api.deps import SessionDep
from app.api.secret_safe import SecretSafeRoute
from app.authz.deps import OrgAdminDep
from app.core.secrets import SecretUnavailable
from app.domains.model import chatgpt
from app.domains.model.client import (
    ModelUnavailable,
)
from app.domains.model.schemas import (
    ChatGPTCallback,
    ChatGPTLogin,
    ConnectionTest,
    ModelSettingPublic,
    ModelSettingUpdate,
)
from app.domains.model.service import (
    InvalidModelSetting,
    check_connection,
    public_setting,
    read_setting,
    save_setting,
)

router = APIRouter(
    prefix="/orgs/{org_id}/model-setting",
    tags=["model-setting"],
    route_class=SecretSafeRoute,
)


@router.get("", response_model=ModelSettingPublic)
def get_model_setting(
    session: SessionDep, principal: OrgAdminDep
) -> ModelSettingPublic:
    return public_setting(read_setting(session, principal.org_id))


@router.put("", response_model=ModelSettingPublic)
def put_model_setting(
    body: ModelSettingUpdate, session: SessionDep, principal: OrgAdminDep
) -> ModelSettingPublic:
    try:
        return save_setting(session, principal, body)
    except (InvalidModelSetting, SecretUnavailable, ModelUnavailable) as error:
        raise HTTPException(422, str(error)) from None


@router.post("/test", response_model=ConnectionTest)
def test_model_setting(session: SessionDep, principal: OrgAdminDep) -> ConnectionTest:
    return check_connection(session, principal)


@router.post("/chatgpt/login", response_model=ChatGPTLogin)
def start_chatgpt_login(session: SessionDep, principal: OrgAdminDep) -> ChatGPTLogin:
    return chatgpt.login(session, principal, start=True)


@router.get("/chatgpt/login", response_model=ChatGPTLogin)
def poll_chatgpt_login(session: SessionDep, principal: OrgAdminDep) -> ChatGPTLogin:
    return chatgpt.login(session, principal)


@router.delete("/chatgpt", response_model=ConnectionTest)
def disconnect_chatgpt(session: SessionDep, principal: OrgAdminDep) -> ConnectionTest:
    chatgpt.disconnect(session, principal)
    return ConnectionTest(
        ok=True,
        message="已断开连接。令牌在 OpenAI 仍有效；如需立即作废，请在 ChatGPT 安全设置里退出这个会话。",
    )


@router.post("/chatgpt/callback", response_model=ChatGPTLogin)
def complete_chatgpt_login(
    body: ChatGPTCallback, session: SessionDep, principal: OrgAdminDep
) -> ChatGPTLogin:
    return chatgpt.login(session, principal, input=body.input.get_secret_value())


@router.delete("/chatgpt/login", response_model=ChatGPTLogin)
def cancel_chatgpt_login(session: SessionDep, principal: OrgAdminDep) -> ChatGPTLogin:
    chatgpt.disconnect(session, principal)
    return ChatGPTLogin(status="none")
