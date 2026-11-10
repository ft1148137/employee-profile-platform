"""Model changes keep credentials outside snapshots and model-visible results."""

from typing import TYPE_CHECKING, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    JsonValue,
    RootModel,
    SecretStr,
    model_validator,
)
from sqlmodel import Session

from app.authz.principal import Principal
from app.core.secrets import decrypt_secret
from app.domains.base import utc_now
from app.domains.drafting.service import active_employees
from app.domains.model import chatgpt, service
from app.domains.model.models import ModelSetting
from app.domains.model.schemas import ModelSettingUpdate

if TYPE_CHECKING:
    from app.domains.admin_assistant.registry import ConnectionTestResult, Risk


class ModelArgs(ModelSettingUpdate):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def subscription_without_key(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("kind") == "chatgpt_subscription":
            return {**value, "api_key": None}
        return value


def snapshot(db: Session, p: Principal, _: Any) -> BaseModel:
    return RootModel(
        service.public_setting(service.read_setting(db, p.org_id)).model_dump(
            mode="json", include=set(ModelArgs.model_fields) - {"api_key"}
        )
    )


def rebinds(current: dict[str, JsonValue], target: dict[str, JsonValue]) -> bool:
    # shortcut: keyless local/compatible destinations need a nonempty key here;
    # use the model page until credential requirements are represented per destination.
    return target.get("kind") != "chatgpt_subscription" and any(
        current.get(k) != target.get(k) for k in ("kind", "provider", "base_url")
    )


def risk(db: Session, p: Principal, args: ModelArgs) -> Risk:
    before = snapshot(db, p, args).model_dump()
    after = args.model_dump()
    return (
        "high"
        if (
            any(
                before[k] != after[k] for k in ("kind", "provider", "model", "base_url")
            )
            or (before["enabled"] and not args.enabled)
        )
        else "normal"
    )


def impact(db: Session, p: Principal, args: ModelArgs) -> BaseModel:
    provider = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "deepseek": "DeepSeek",
        "qwen": "通义千问",
        "moonshotai": "Kimi 国际",
        "moonshotai-cn": "Kimi 中国",
        "kimi-coding": "Kimi Coding",
        "openrouter": "OpenRouter",
        "ollama": "本地模型",
        "openai-compatible": "其他兼容模型服务",
    }.get(args.provider or "", "ChatGPT")
    text = (
        "管理助手和自动起草会停用，网页手工功能照常可用"
        if not args.enabled
        else f"数据将发送给 {provider}"
        + (f"（{args.base_url}）" if args.base_url else "")
        + "。发给模型的内容：能力词典，以及工作记录的标题、类型、状态、标签、项目、日期和说明；不发姓名、工号、邮箱和外部账号。"
    )
    return RootModel(
        {
            "count": len(active_employees(db, p.org_id)),
            "text": text + "；撤销只恢复非密钥字段，密钥不回滚",
        }
    )


def test_model(db: Session, p: Principal, args: ModelArgs) -> ConnectionTestResult:
    from app.domains.admin_assistant.registry import ConnectionTestResult

    saved = service.read_setting(db, p.org_id)
    before = snapshot(db, p, args).model_dump(mode="json")
    if args.kind == "chatgpt_subscription":
        # A draft probe must not refresh credentials or change the saved model.
        if chatgpt.local_auth():
            key = "local-codex"
        else:
            if (
                not saved
                or not saved.chatgpt_auth_ciphertext
                or (saved.chatgpt_account or {}).get("needs_relogin")
            ):
                return ConnectionTestResult(
                    ok=False, message="请先在设置页连接 ChatGPT"
                )
            tokens = chatgpt.OAuthTokens.model_validate_json(
                decrypt_secret(saved.chatgpt_auth_ciphertext)
            )
            if tokens.expires <= (utc_now().timestamp() + 300) * 1000:
                return ConnectionTestResult(
                    ok=False, message="请先在设置页连接 ChatGPT"
                )
            key = tokens.access
    elif args.api_key is not None:
        key = args.api_key.get_secret_value()
    elif rebinds(before, args.model_dump(mode="json")):
        return ConnectionTestResult(ok=False, message="请先填写新的凭据")
    else:
        key = (
            decrypt_secret(saved.api_key_ciphertext)
            if saved and saved.api_key_ciphertext
            else ""
        )
    if args.enabled and args.kind == "cloud_api" and not key:
        return ConnectionTestResult(ok=False, message="请填写模型 API 密钥")
    assert p.user_id is not None
    candidate = ModelSetting(
        org_id=p.org_id,
        updated_by_user_id=p.user_id,
        **args.model_dump(exclude={"api_key"}),
    )
    result = service.test_connection(
        service.model_config(candidate, api_key=SecretStr(key))
    )
    return ConnectionTestResult(
        ok=result.ok,
        message="模型连接成功"
        if result.ok
        else "模型连接失败，请检查密钥、模型设置和网络",
    )
