import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from types import ModuleType

from pydantic import BaseModel, ValidationError


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


@dataclass(frozen=True)
class ChatResult[T: BaseModel]:
    value: T
    provider: str
    model: str
    attempts: int


def _sdk() -> ModuleType:
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS"] = "True"
    os.environ["LITELLM_LOG"] = "ERROR"
    import litellm

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
            provider = sdk.get_llm_provider(model=config.model)[1]
            response_format = (
                {"response_format": output}
                if sdk.supports_response_schema(model=config.model)
                else {}
            )
            response = sdk.completion(
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
                **response_format,
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
                retry_at = _retry_at(error)
                if attempt == attempts:
                    raise ModelRateLimited(retry_at) from None
                delay = (
                    (retry_at - datetime.now(UTC)).total_seconds() if retry_at else 1
                )
                time.sleep(delay if 0 <= delay <= 60 else 1)
                continue
            if isinstance(
                error,
                (
                    sdk.Timeout,
                    sdk.InternalServerError,
                    sdk.ServiceUnavailableError,
                    sdk.BadGatewayError,
                ),
            ):
                reason = "模型服务暂时不可用"
            elif isinstance(error, sdk.APIConnectionError):
                reason = "连不上模型服务"
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
