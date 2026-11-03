from collections.abc import Callable, Coroutine
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep
from app.core.secrets import SecretUnavailable
from app.domains.model.client import (
    ModelOutputInvalid,
    ModelRateLimited,
    ModelUnavailable,
    chat_json,
)
from app.domains.model.schemas import (
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


class SecretSafeRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                # FastAPI's default validation response echoes the input, including keys.
                raise HTTPException(
                    422,
                    [{k: e[k] for k in ("loc", "msg", "type")} for e in error.errors()],
                ) from None

        return handle


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
