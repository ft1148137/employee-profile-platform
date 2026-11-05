import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr

from app.core.config import settings
from app.domains.model.client import (
    ModelAuthenticationError,
    ModelRateLimited,
    ModelServiceError,
    ModelUnavailable,
)

logger = logging.getLogger(__name__)


class PiModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org_id: UUID | None = Field(default=None, exclude=True)
    provider: str
    api: Literal[
        "openai-responses",
        "anthropic-messages",
        "openai-completions",
        "openai-codex-responses",
    ]
    id: str
    baseUrl: str
    api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    thinkingLevel: Literal["off", "minimal", "low", "medium", "high", "xhigh"] = "off"
    reasoning: bool = False
    contextWindow: int = Field(default=128000, gt=0)
    maxTokens: int = Field(default=4096, gt=0)
    compat: dict[str, JsonValue] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str
    parameters: dict[str, JsonValue]


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    text: str = Field(max_length=24000)


class PiBusy(ModelUnavailable):
    pass


class TurnResult(BaseModel):
    text: str
    stop_reason: Literal["stop", "length", "toolUse", "aborted"]
    model_calls: int = Field(ge=0, le=8)
    tool_calls: int = Field(ge=0, le=16)


def _error(event: dict[str, JsonValue]) -> None:
    value = event.get("code")
    code = value if isinstance(value, str) else ""
    if code == "busy":
        raise PiBusy("模型服务繁忙，请稍后再试")

    if code == "model_unauthorized":
        raise ModelAuthenticationError()
    match code:
        case "model_not_found" | "upstream_rejected" | "upstream_unavailable":
            raise ModelServiceError(code)
    if code == "rate_limited":
        retry_at = datetime.fromisoformat(str(event["retry_at"]))
        if retry_at.tzinfo is None:
            raise ValueError("Invalid retry time")
        reason = "模型用量到上限" + (
            "；恢复时间为估计" if event.get("estimated") else ""
        )
        raise ModelRateLimited(retry_at, reason)
    if code in ("unauthorized", "bad_request"):
        logger.error("Pi protocol failure: %s", code)
    messages = {
        "model_forbidden": "密钥不对或没有权限",
        "busy": "模型服务繁忙，请稍后再试",
        "deadline": "模型调用超时",
        "limit_exceeded": "模型调用次数达到上限",
        "cancelled": "模型调用已取消",
        "local_auth_unavailable": "本机 Codex 授权不可用或有效期不足一小时，请在本机正常运行一次 codex 刷新",
    }
    raise ModelUnavailable(messages.get(str(code), "模型服务暂时不可用"))


def run_turn(
    config: PiModelConfig,
    *,
    mode: Literal["draft", "chat"] = "draft",
    history: list[HistoryMessage] | None = None,
    system_prompt: str,
    input: str,
    tools: list[ToolDefinition],
    on_tool_call: Callable[[str, dict[str, JsonValue]], JsonValue],
) -> TurnResult:
    run_id = str(uuid4())
    path = f"/v1/turns/{run_id}"
    model = config.model_dump(mode="json", exclude={"api_key"}, exclude_unset=True)
    model["api_key"] = config.api_key.get_secret_value()
    body = {
        "version": 1,
        "run_id": run_id,
        "mode": mode,
        "model": model,
        "system_prompt": system_prompt,
        "input": input,
        "tools": [tool.model_dump(mode="json") for tool in tools],
    }
    if history is not None:
        if (
            mode != "chat"
            or len(history) > 20
            or sum(len(m.text) for m in history) > 24000
        ):
            raise ValueError("Invalid conversation history")
        body["history"] = [m.model_dump(mode="json") for m in history]
    terminal = False
    names = {tool.name for tool in tools}
    try:
        with httpx.Client(
            base_url=settings.PI_URL,
            headers={
                "Authorization": f"Bearer {settings.PI_SERVICE_TOKEN.get_secret_value()}"
            },
            timeout=httpx.Timeout(70 if mode == "chat" else 130, connect=5),
            trust_env=False,
        ) as client:
            with client.stream("POST", "/v1/turns", json=body) as response:
                try:
                    if response.status_code != 200:
                        terminal = True
                        response.read()
                        _error(response.json())
                    chunks: list[str] = []
                    lines = response.iter_lines()
                    for line in lines:
                        event = json.loads(line)
                        match event["type"]:
                            case "text":
                                chunks.append(event["text"])
                            case "tool_call":
                                if event["name"] not in names or not isinstance(
                                    event["arguments"], dict
                                ):
                                    raise ValueError("Invalid tool call")
                                try:
                                    result = on_tool_call(
                                        event["name"], event["arguments"]
                                    )
                                except Exception:
                                    client.post(
                                        path
                                        + "/results/"
                                        + quote(event["call_id"], safe=""),
                                        json={
                                            "result": "平台工具调用失败",
                                            "is_error": True,
                                        },
                                    ).raise_for_status()
                                    raise ModelUnavailable("平台工具调用失败") from None
                                client.post(
                                    path
                                    + "/results/"
                                    + quote(event["call_id"], safe=""),
                                    json={"result": result, "is_error": False},
                                ).raise_for_status()
                            case "error":
                                terminal = True
                                _error(event)
                            case "done":
                                terminal = True
                                return TurnResult(
                                    text="".join(chunks),
                                    stop_reason=event["stopReason"],
                                    model_calls=event["model_calls"],
                                    tool_calls=event["tool_calls"],
                                )
                            case _:
                                raise ValueError("Invalid Pi event")
                    raise ModelUnavailable("模型服务连接中断")
                finally:
                    if not terminal:
                        # DELETE acknowledges only after abort and slot release.
                        cancelled = client.delete(path)
                        if cancelled.status_code == 404:
                            # The model may finish between a tool error and DELETE.
                            if not any(
                                json.loads(line).get("type") in ("done", "error")
                                for line in lines
                            ):
                                raise ModelUnavailable("模型服务连接中断")
                        else:
                            cancelled.raise_for_status()
    except ModelAuthenticationError:
        if config.org_id is not None:
            from app.domains.model.chatgpt import RELOGIN, invalidate

            invalidate(config.org_id, config.api_key.get_secret_value())
            raise ModelUnavailable(RELOGIN) from None
        raise
    except httpx.HTTPError, ValueError, KeyError, TypeError:
        raise ModelUnavailable("模型服务连接失败或协议错误") from None
