from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.api.secret_safe import SecretSafeRoute
from app.authz.deps import OrgAdminDep
from app.core.secrets import SecretUnavailable
from app.domains.model import chatgpt
from app.domains.model.client import (
    ModelOutputInvalid,
    ModelRateLimited,
    ModelUnavailable,
    chat_json,
)
from app.domains.model.schemas import (
    ChatGPTLogin,
    ConnectionTest,
    ModelSettingPublic,
    ModelSettingUpdate,
)
from app.domains.model.service import (
    InvalidModelSetting,
    model_config,
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
    except (InvalidModelSetting, SecretUnavailable) as error:
        raise HTTPException(422, str(error)) from None


class ModelTestOutput(BaseModel):
    ok: Literal[True]


@router.post("/test", response_model=ConnectionTest)
def test_model_setting(session: SessionDep, principal: OrgAdminDep) -> ConnectionTest:
    try:
        config = model_config(read_setting(session, principal.org_id))
        chat_json(
            config,
            [{"role": "user", "content": '只返回 {"ok": true}'}],
            ModelTestOutput,
            attempts=1,
        )
    except (
        SecretUnavailable,
        ModelUnavailable,
        ModelOutputInvalid,
        ModelRateLimited,
    ) as error:
        return ConnectionTest(ok=False, message=str(error))
    return ConnectionTest(ok=True, message="模型连接成功")


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
