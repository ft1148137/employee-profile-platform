import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import jwt
from fastapi import FastAPI, HTTPException
from mcp import types
from mcp.server import Server
from mcp.server.context import ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import (
    RequestBodyLimitMiddleware,
    TransportSecuritySettings,
)
from pydantic import ValidationError
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from app.authz.principal import Principal
from app.core.config import settings
from app.core.db import engine
from app.core.security import ALGORITHM
from app.domains.access_token.auth import token_principal
from app.domains.access_token.models import PersonalAccessToken
from app.domains.access_token.service import get_setting
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.change_request.models import ReviewVia
from app.domains.employee_tools import EMPLOYEE_TOOLS
from app.domains.manager_tools import MANAGER_TOOLS
from app.domains.org.employee_fields import FIELD_LABELS
from app.mcp.oauth import resource_url

TOOLS = {tool.name: tool for tool in (*EMPLOYEE_TOOLS, *MANAGER_TOOLS)}


class McpRequestError(HTTPException):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(status, message)


async def list_tools(
    _ctx: ServerRequestContext[Any, Request],
    _params: types.PaginatedRequestParams | None,
) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name=t.name,
                description=t.description,
                input_schema=t.input_model.model_json_schema(),
            )
            for t in TOOLS.values()
        ]
    )


def invoke(
    ctx: ServerRequestContext[Any, Request], params: types.CallToolRequestParams
) -> types.CallToolResult:
    assert ctx.request is not None
    principal, client_name = ctx.request.scope["mcp_identity"]
    with Session(engine) as session:
        try:
            record_audit(
                session,
                org_id=principal.org_id,
                actor_type=principal.actor_type,
                actor_id=str(principal.user_id),
                action="mcp.tool_called",
                target_type="mcp_tool",
                target_id=params.name if params.name in TOOLS else None,
                details={"request_id": str(ctx.request_id), "client_name": client_name},
            )
            session.commit()
            tool = TOOLS.get(params.name)
            if tool is None:
                raise McpRequestError(404, "工具不存在")
            body = tool.input_model.model_validate(params.arguments or {})
            arguments = {key: getattr(body, key) for key in type(body).model_fields}
            if tool.name in ("draft_update_request", "submit_request"):
                arguments.update(via="mcp", client_name=client_name)
            if tool.name == "decide_review_items":
                arguments.update(via=ReviewVia.MCP, client_name=client_name)
            result = tool.handler(principal=principal, session=session, **arguments)
            session.commit()
            data = result.model_dump(
                mode="json",
                exclude={
                    "employee": {str(f) for f in FIELD_LABELS},
                    "department_capabilities_configured": True,
                    "items": {"__all__": {"in_department"}},
                }
                if tool.name == "get_my_profile"
                else None,
            )
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text", text=json.dumps(data, ensure_ascii=False)
                    )
                ],
                structured_content=data,
            )
        except ValidationError:
            message = "工具参数无效，请核对字段和格式"
        except HTTPException as exc:
            message = json.dumps(
                {"status": exc.status_code, "detail": exc.detail}, ensure_ascii=False
            )
        except Exception:
            # Never let SDK exception logging include SQL, credentials or input text.
            message = "操作失败，请稍后重试"
        session.rollback()
    return types.CallToolResult(
        is_error=True, content=[types.TextContent(type="text", text=message)]
    )


async def call_tool(
    ctx: ServerRequestContext[Any, Request], params: types.CallToolRequestParams
) -> types.CallToolResult:
    return await run_in_threadpool(invoke, ctx, params)


server: Server[dict[str, Any]] = Server(
    "employee-profile",
    version="0.1.0",
    on_list_tools=list_tools,
    on_call_tool=call_tool,
)


class McpEndpoint:
    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        try:
            authorization = request.headers.get("authorization", "")
            scheme, _, plaintext = authorization.partition(" ")
            if scheme.lower() != "bearer":
                plaintext = ""
            ip = request.client.host if request.client else "unknown"

            fingerprint = hashlib.sha256(plaintext.encode()).hexdigest()

            def authenticate() -> tuple[Principal, list[str], str | None]:
                with Session(engine) as session:
                    principal = token_principal(session, plaintext, ip)
                    oauth_name = session.exec(
                        select(PersonalAccessToken.name).where(
                            PersonalAccessToken.org_id == principal.org_id,
                            PersonalAccessToken.user_id == principal.user_id,
                            PersonalAccessToken.token_hash == fingerprint,
                            PersonalAccessToken.client_kind == "oauth",
                        )
                    ).one_or_none()
                    return (
                        principal,
                        get_setting(session, principal).allowed_clients,
                        oauth_name,
                    )

            principal, allowed_clients, oauth_name = await run_in_threadpool(
                authenticate
            )
            session_id = request.headers.get("mcp-session-id")
            if session_id:
                try:
                    claims = jwt.decode(
                        session_id,
                        settings.SECRET_KEY,
                        algorithms=[ALGORITHM],
                        audience="mcp-session",
                        options={"require": ["exp", "sub", "client_name"]},
                    )
                except jwt.InvalidTokenError:
                    raise McpRequestError(404, "会话已失效，请重新连接")
                if claims["sub"] != fingerprint:
                    raise McpRequestError(404, "会话已失效，请重新连接")
                client_name = claims["client_name"]
            else:
                if request.method != "POST":
                    raise McpRequestError(400, "请先初始化 MCP 会话")
                try:
                    message = await request.json()
                    if (
                        not isinstance(message, dict)
                        or message.get("method") != "initialize"
                    ):
                        raise ValueError
                    params = types.InitializeRequestParams.model_validate(
                        message.get("params")
                    )
                    client_name = oauth_name or params.client_info.name
                    if not 1 <= len(client_name) <= 100:
                        raise ValueError
                except ValueError, ValidationError:
                    raise McpRequestError(400, "初始化参数无效，请重新连接")
                session_id = jwt.encode(
                    {
                        "aud": "mcp-session",
                        "sub": fingerprint,
                        "client_name": client_name,
                        "exp": utc_now() + timedelta(hours=1),
                    },
                    settings.SECRET_KEY,
                    algorithm=ALGORITHM,
                )
            if allowed_clients and client_name not in allowed_clients:
                raise McpRequestError(403, "企业未允许此客户端")
            scope["mcp_identity"] = principal, client_name

            async def send_response(message: Message) -> None:
                if message["type"] == "http.response.start":
                    message["headers"].extend(
                        [
                            (b"mcp-session-id", session_id.encode()),
                            (b"cache-control", b"no-store"),
                        ]
                    )
                await send(message)

            cached_body = (
                await request.body()
                if not request.headers.get("mcp-session-id")
                else None
            )

            async def replay_body() -> Message:
                nonlocal cached_body
                if cached_body is not None:
                    body, cached_body = cached_body, None
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            await self.manager.handle_request(scope, replay_body, send_response)
        except HTTPException as exc:
            if exc.status_code == 401:
                exc.headers = {
                    "WWW-Authenticate": f'Bearer resource_metadata="{resource_url().removesuffix("/mcp")}/.well-known/oauth-protected-resource"'
                }
            await JSONResponse(
                {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
            )(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    manager = StreamableHTTPSessionManager(
        server,
        stateless=True,
        json_response=True,
        security_settings=TransportSecuritySettings(
            allowed_hosts=[
                urlsplit(resource_url()).netloc,
                "localhost:*",
                "127.0.0.1:*",
                "testserver",
            ],
            allowed_origins=[resource_url().removesuffix("/mcp")],
        ),
    )
    app.state.mcp = RequestBodyLimitMiddleware(McpEndpoint(manager), 4 * 1024 * 1024)
    async with manager.run():
        yield


class McpApplication:
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await scope["app"].state.mcp(scope, receive, send)


endpoint = McpApplication()
