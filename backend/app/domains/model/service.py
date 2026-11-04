from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
from pydantic import SecretStr
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.config import settings
from app.core.secrets import decrypt_secret, encrypt_secret
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.model import chatgpt
from app.domains.model.client import ModelUnavailable
from app.domains.model.models import ModelSetting
from app.domains.model.pi_client import PiModelConfig
from app.domains.model.schemas import (
    CLOUD_PROVIDERS,
    ChatGPTStatus,
    ModelSettingPublic,
    ModelSettingUpdate,
)
from app.domains.org.models import Organization


class InvalidModelSetting(Exception):
    pass


def read_setting(session: Session, org_id: UUID) -> ModelSetting | None:
    return session.exec(
        select(ModelSetting).where(ModelSetting.org_id == org_id)
    ).first()


def public_setting(setting: ModelSetting | None) -> ModelSettingPublic:
    if setting is None:
        return ModelSettingPublic()
    return ModelSettingPublic(
        **setting.model_dump(exclude={"api_key_ciphertext"}),
        has_api_key=bool(setting.api_key_ciphertext),
        chatgpt=ChatGPTStatus(
            connected=bool(setting.chatgpt_auth_ciphertext),
            **(setting.chatgpt_account or {}),
        ),
    )


def save_setting(
    session: Session, principal: Principal, body: ModelSettingUpdate
) -> ModelSettingPublic:
    assert principal.user_id is not None
    # Lock the organization too: concurrent first saves must serialize.
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    setting = read_setting(session, principal.org_id)
    if setting is None:
        setting = ModelSetting(
            org_id=principal.org_id, updated_by_user_id=principal.user_id
        )
    if (
        body.enabled
        and body.kind == "chatgpt_subscription"
        and not (
            setting.chatgpt_auth_ciphertext
            and not (setting.chatgpt_account or {}).get("needs_relogin")
        )
    ):
        raise InvalidModelSetting("请先在设置页连接 ChatGPT")
    changed = body.api_key is not None
    token = setting.api_key_ciphertext
    if (
        not changed
        and token
        and body.kind != "chatgpt_subscription"
        and (
            body.kind != setting.kind
            or body.provider != setting.provider
            or (body.base_url or "").rstrip("/") != (setting.base_url or "").rstrip("/")
        )
    ):
        raise InvalidModelSetting("更换了模型类型、提供方或服务地址，请重新填写密钥")
    if changed:
        assert body.api_key is not None
        key = body.api_key.get_secret_value()
        token = encrypt_secret(key) if key else None
    if body.kind == "chatgpt_subscription":
        token = None
    if body.enabled:
        if body.kind == "cloud_api" and not token:
            raise InvalidModelSetting("请填写模型 API 密钥")
        if body.provider == "anthropic" and token:
            if decrypt_secret(token).startswith("sk-ant-oat"):
                raise InvalidModelSetting(
                    "不支持 Claude 订阅登录，请使用 Anthropic API key"
                )
    if body.enabled and body.kind != "chatgpt_subscription":
        validate_capabilities(body.provider, body.model, body.reasoning_effort)
    setting.sqlmodel_update(body.model_dump(exclude={"api_key"}))
    setting.api_key_ciphertext = token
    setting.updated_by_user_id = principal.user_id
    setting.updated_at = utc_now()
    session.add(setting)
    try:
        hostname = urlsplit(body.base_url).hostname if body.base_url else None
    except ValueError:
        hostname = None  # Disabled settings may be incomplete.
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="model_setting.updated",
        target_type="model_setting",
        target_id=str(principal.org_id),
        details={
            "enabled": body.enabled,
            "kind": body.kind,
            "model": body.model,
            "provider": body.provider,
            "base_url": hostname,
            "api_key_changed": changed,
        },
    )
    session.commit()
    session.refresh(setting)
    return public_setting(setting)


def model_config(setting: ModelSetting | None) -> PiModelConfig:
    if setting is None or not setting.enabled or not setting.model:
        raise ModelUnavailable("模型已关闭，请先启用模型")
    if setting.kind != "chatgpt_subscription":
        if not setting.provider:
            raise ModelUnavailable("请重新选择提供方")
        api, base_url = CLOUD_PROVIDERS.get(
            setting.provider, ("openai-completions", setting.base_url)
        )
        if setting.provider == "qwen":
            base_url = setting.base_url
        if not base_url:
            raise ModelUnavailable("请填写模型服务地址")
        if setting.provider == "ollama":
            base_url = base_url.rstrip("/").removesuffix("/v1") + "/v1"
        config = PiModelConfig.model_validate(
            {
                "provider": setting.provider,
                "api": api,
                "id": setting.model,
                "baseUrl": base_url,
                "api_key": SecretStr(
                    decrypt_secret(setting.api_key_ciphertext)
                    if setting.api_key_ciphertext
                    else ""
                ),
                "thinkingLevel": setting.reasoning_effort or "off",
            }
        )
        if setting.provider in ("qwen", "openai-compatible", "ollama"):
            config.reasoning = config.thinkingLevel != "off"
            config.contextWindow, config.maxTokens = 128000, 4096
            config.compat = {"supportsReasoningEffort": True}
        return config
    return PiModelConfig.model_validate(
        {
            "provider": "openai-codex",
            "api": "openai-codex-responses",
            "id": setting.model.removeprefix("chatgpt/"),
            "baseUrl": "https://chatgpt.com/backend-api",
            "api_key": SecretStr(chatgpt.access_token(setting.org_id)),
            "org_id": setting.org_id,
            "thinkingLevel": setting.reasoning_effort or "high",
        }
    )


def validate_capabilities(
    provider: str | None, model: str | None, effort: str | None
) -> None:
    if not provider or not model:
        raise ModelUnavailable("请重新选择提供方")
    try:
        response = httpx.get(
            f"{settings.PI_URL}/v1/models/{quote(provider, safe='')}/{quote(model, safe='')}",
            headers={
                "Authorization": f"Bearer {settings.PI_SERVICE_TOKEN.get_secret_value()}"
            },
            timeout=5,
            trust_env=False,
        )
        if response.status_code == 400:
            raise ModelUnavailable("该模型不在支持的列表里")
        response.raise_for_status()
        capabilities = response.json()
        if (effort or "off") not in capabilities["thinking_levels"]:
            raise ModelUnavailable(
                f"推理强度 {effort} 不适用于该模型，请在模型设置里修改"
            )
    except httpx.HTTPError, ValueError, KeyError, TypeError:
        raise ModelUnavailable("模型服务暂时不可用，请稍后再试") from None
