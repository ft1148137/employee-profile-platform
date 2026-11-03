import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from functools import partial
from types import ModuleType
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.domains.model import chatgpt


class ModelUnavailable(Exception):
    pass


class ModelOutputInvalid(Exception):
    def __init__(self) -> None:
        super().__init__("模型输出不符合格式")


class ModelRateLimited(Exception):
    def __init__(
        self, retry_at: datetime | None, reason: str = "模型用量到上限"
    ) -> None:
        self.retry_at = retry_at
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    org_id: UUID | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ChatResult[T: BaseModel]:
    value: T
    provider: str
    model: str
    attempts: int


class _AuthLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.pathname.endswith("/chatgpt/authenticator.py")


_AUTH_LOG_FILTER = _AuthLogFilter()


def _sdk() -> ModuleType:
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS"] = "True"
    os.environ["LITELLM_LOG"] = "ERROR"
    os.environ["CHATGPT_TOKEN_DIR"] = str(chatgpt.TOKEN_DIR)
    os.environ["CHATGPT_AUTH_FILE"] = "auth.json"
    import litellm

    # The SDK refresh warning includes the upstream error body, which may contain credentials.
    logging.getLogger("LiteLLM").addFilter(_AUTH_LOG_FILTER)
    litellm.callbacks = []
    litellm.success_callback = []
    litellm.failure_callback = []
    litellm.turn_off_message_logging = True
    litellm.suppress_debug_info = True
    return litellm


def _retry_at(error: Exception) -> datetime | None:
    response = getattr(error, "response", None)
    value = getattr(response, "headers", {}).get("retry-after")
    if not value:
        return None
    try:
        return datetime.now(UTC) + timedelta(seconds=max(0, float(value)))
    except ValueError, OverflowError:
        try:
            date = parsedate_to_datetime(value)
            return date.replace(tzinfo=UTC) if date.tzinfo is None else date
        except ValueError, TypeError, OverflowError:
            return None


def rate_limit(error: Exception) -> tuple[datetime, str]:
    retry_at = _retry_at(error)
    response = getattr(error, "response", None)
    data: dict[str, Any] = {}
    try:
        data = response.json() if response is not None else {}
        data = data.get("error", data)
        if not isinstance(data, dict):
            raise ValueError("Invalid limit response")
        if retry_at is None:
            if "resets_in_seconds" in data:
                retry_at = datetime.now(UTC) + timedelta(
                    seconds=max(0, float(data["resets_in_seconds"]))
                )
            elif "resets_at" in data:
                retry_at = datetime.fromtimestamp(float(data["resets_at"]), UTC)
    except ValueError, TypeError, AttributeError, OverflowError, OSError:
        data = data if isinstance(data, dict) else {}
    window = data.get("limit_window_minutes")
    reason = (
        "本周用量已到上限"
        if window == 10080
        else "5 小时用量已到上限"
        if window == 300
        else "请求过于频繁"
    )
    if retry_at is None:
        return datetime.now(UTC) + timedelta(
            minutes=30
        ), reason + "；预计恢复时间未知，30 分钟后重试"
    return retry_at, reason


def chat_json[T: BaseModel](
    config: ModelConfig,
    messages: list[dict[str, str]],
    output: type[T],
    *,
    attempts: int = 3,
) -> ChatResult[T]:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    sdk = _sdk()
    conversation = [
        {
            "role": "system",
            "content": "只返回符合此 JSON Schema 的 JSON："
            + json.dumps(output.model_json_schema()),
        },
        *[dict(message) for message in messages],
    ]
    for attempt in range(1, attempts + 1):
        try:
            # Provider detection itself authenticates chatgpt; defer it until the token lock.
            provider = (
                "chatgpt"
                if config.model.startswith("chatgpt/")
                else sdk.get_llm_provider(model=config.model)[1]
            )
            response_format = (
                {"response_format": output}
                if provider != "chatgpt"
                and sdk.supports_response_schema(model=config.model)
                else {}
            )

            invoke = partial(
                sdk.completion,
                model=config.model,
                messages=conversation,
                api_base=config.base_url,
                # OpenAI's SDK requires a nonempty key even for unauthenticated servers.
                api_key=config.api_key
                or ("not-needed" if config.base_url and provider == "openai" else None),
                temperature=0,
                timeout=120,
                num_retries=0,
                drop_params=True,
                **(
                    {"reasoning_effort": config.reasoning_effort}
                    if config.reasoning_effort
                    else {}
                ),
                **response_format,
            )

            response = (
                chatgpt.completion(config.org_id, invoke)
                if provider == "chatgpt"
                else invoke()
            )
        except tuple(sdk.LITELLM_EXCEPTION_TYPES) as error:
            if isinstance(error, (sdk.AuthenticationError, sdk.PermissionDeniedError)):
                raise ModelUnavailable("密钥不对或没有权限") from None
            if isinstance(error, sdk.NotFoundError) or (
                isinstance(error, sdk.BadRequestError)
                and any(
                    term in str(error).lower()
                    for term in (
                        "model_not_found",
                        "model not found",
                        "does not exist",
                        "invalid model",
                    )
                )
            ):
                raise ModelUnavailable("地址或模型名不对") from None
            if isinstance(error, sdk.RateLimitError):
                retry_at, reason = rate_limit(error)
                if provider == "chatgpt" or attempt == attempts:
                    raise ModelRateLimited(retry_at, reason) from None
                delay = (
                    (retry_at - datetime.now(UTC)).total_seconds() if retry_at else 1
                )
                time.sleep(delay if 0 <= delay <= 60 else 1)
                continue
            if isinstance(error, sdk.APIConnectionError) or (
                isinstance(error, sdk.InternalServerError)
                and "connection error" in str(error).lower()
            ):
                # LiteLLM also wraps refused connections as InternalServerError.
                reason = "连不上模型服务"
            elif isinstance(
                error,
                (
                    sdk.Timeout,
                    sdk.InternalServerError,
                    sdk.ServiceUnavailableError,
                    sdk.BadGatewayError,
                ),
            ):
                reason = "模型服务暂时不可用"
            else:
                raise ModelUnavailable(
                    f"模型调用失败（{type(error).__name__}）"
                ) from None
            if attempt == attempts:
                raise ModelUnavailable(reason) from None
            continue
        content = response.choices[0].message.content or ""
        clean = content.strip()
        if clean.startswith("```") and clean.endswith("```"):
            clean = (
                clean.removeprefix("```")
                .removeprefix("json")
                .strip()
                .removesuffix("```")
                .strip()
            )
        try:
            value = output.model_validate(json.loads(clean))
        except ValueError, ValidationError:
            if attempt == attempts:
                raise ModelOutputInvalid() from None
            conversation.extend(
                [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "上次输出不符合要求：JSON 格式或字段校验失败。只返回符合要求的 JSON。",
                    },
                ]
            )
            continue
        return ChatResult(value, provider, response.model or config.model, attempt)
    raise AssertionError("unreachable")
