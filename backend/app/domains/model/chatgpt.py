import json
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.config import settings
from app.core.db import engine
from app.core.secrets import SecretUnavailable, decrypt_secret, encrypt_secret
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.model.client import ModelUnavailable
from app.domains.model.models import ModelSetting
from app.domains.model.schemas import ChatGPTAccount, ChatGPTLogin
from app.domains.org.models import Organization

RELOGIN = "ChatGPT 登录已失效，请在设置页重新连接"
AUTH_PATH = "/v1/auth/openai-codex/logins"


class OAuthTokens(BaseModel):
    access: str = Field(min_length=1, repr=False)
    refresh: str = Field(min_length=1, repr=False)
    expires: float = Field(gt=0, allow_inf_nan=False)
    accountId: str = Field(min_length=1)


def auth_request(method: str, path: str = "", **body: str) -> dict[str, Any]:
    try:
        response = httpx.request(
            method,
            settings.PI_URL + AUTH_PATH + path,
            json=body if method == "POST" else None,
            headers={
                "Authorization": f"Bearer {settings.PI_SERVICE_TOKEN.get_secret_value()}"
            },
            timeout=15,
            trust_env=False,
        )
        data = response.json()
        if response.is_success and isinstance(data, dict):
            return data
        messages = {
            "login_busy": "已有连接流程，请稍后再试",
            "login_not_found": "登录流程已失效，请重新开始",
        }
        raise ModelUnavailable(
            messages.get(data.get("code"), "ChatGPT 登录失败，请重新开始")
        )
    except httpx.HTTPError, ValueError, TypeError, AttributeError:
        raise ModelUnavailable("ChatGPT 登录服务暂时不可用，请重新开始") from None


def locked_setting(session: Session, principal: Principal) -> ModelSetting:
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    setting = session.exec(
        select(ModelSetting)
        .where(ModelSetting.org_id == principal.org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if setting is None:
        assert principal.user_id is not None
        setting = ModelSetting(
            org_id=principal.org_id, updated_by_user_id=principal.user_id
        )
        session.add(setting)
    return setting


def clear_login(setting: ModelSetting) -> None:
    setting.chatgpt_login_ciphertext = None
    setting.chatgpt_login_expires_at = setting.chatgpt_login_polled_at = None


def needs_relogin(setting: ModelSetting) -> None:
    setting.chatgpt_account = {**(setting.chatgpt_account or {}), "needs_relogin": True}


def login(
    session: Session,
    principal: Principal,
    *,
    start: bool = False,
    input: str | None = None,
) -> ChatGPTLogin:
    setting = locked_setting(session, principal)
    try:
        active = None
        if setting.chatgpt_login_ciphertext:
            active = json.loads(decrypt_secret(setting.chatgpt_login_ciphertext))
            if (
                active["user_id"] != str(principal.user_id)
                and setting.chatgpt_login_expires_at
                and setting.chatgpt_login_expires_at > utc_now()
            ):
                return ChatGPTLogin(
                    status="error", message="已有其他管理员发起的连接流程，请稍后再试"
                )
            if (
                not setting.chatgpt_login_expires_at
                or setting.chatgpt_login_expires_at <= utc_now()
            ):
                try:
                    auth_request("DELETE", "/" + quote(active["login_id"], safe=""))
                except ModelUnavailable:
                    pass  # Pi may already have expired the same flow.
                clear_login(setting)
                active = None
                if not start:
                    return ChatGPTLogin(
                        status="expired", message="登录已过期，请重新开始"
                    )
        if input is not None:
            if active is None:
                return ChatGPTLogin(
                    status="error", message="登录流程已失效，请重新开始"
                )
            # Consume even invalid callbacks; another administrator cannot consume this flow.
            clear_login(setting)
            url = urlsplit(input)
            params = parse_qs(url.query)
            if (
                url.scheme != "http"
                or url.netloc != "localhost:1455"
                or url.path != "/auth/callback"
                or len(params.get("code", [])) != 1
                or params.get("state") != [active["state"]]
            ):
                auth_request("DELETE", "/" + quote(active["login_id"], safe=""))
                raise ModelUnavailable(
                    "请粘贴本次登录的完整重定向网址（包含正确的 code 和 state），然后重新开始"
                )
            tokens = OAuthTokens.model_validate(
                auth_request(
                    "POST", "/" + quote(active["login_id"], safe=""), input=input
                )
            )
            setting.chatgpt_auth_ciphertext = encrypt_secret(tokens.model_dump_json())
            setting.chatgpt_account = {
                "account_id": tokens.accountId,
                "needs_relogin": False,
            }
            record_audit(
                session,
                org_id=principal.org_id,
                actor_type=principal.actor_type,
                actor_id=str(principal.user_id),
                action="model_setting.chatgpt_connected",
                target_type="model_setting",
                target_id=str(principal.org_id),
                details={},
            )
        elif start and active is None:
            result = auth_request("POST")
            state = parse_qs(urlsplit(result["url"]).query)["state"]
            if len(state) != 1 or not state[0]:
                raise ValueError("Missing state")
            active = {**result, "state": state[0], "user_id": str(principal.user_id)}
            setting.chatgpt_login_ciphertext = encrypt_secret(json.dumps(active))
            setting.chatgpt_login_expires_at = utc_now() + timedelta(minutes=15)
        if active is not None and input is None:
            return ChatGPTLogin(
                status="pending",
                verification_url=active["url"],
                expires_at=setting.chatgpt_login_expires_at,
            )
        if setting.chatgpt_auth_ciphertext:
            tokens = OAuthTokens.model_validate_json(
                decrypt_secret(setting.chatgpt_auth_ciphertext)
            )
            if tokens.expires <= (utc_now().timestamp() + 300) * 1000:
                needs_relogin(setting)
            return ChatGPTLogin(
                status="connected",
                account=ChatGPTAccount(**(setting.chatgpt_account or {})),
            )
        return ChatGPTLogin(status="none")
    except SecretUnavailable, ValidationError, ValueError, KeyError, TypeError:
        clear_login(setting)
        needs_relogin(setting)
        return ChatGPTLogin(
            status="error", message=RELOGIN, account=ChatGPTAccount(needs_relogin=True)
        )
    except ModelUnavailable as error:
        clear_login(setting)
        return ChatGPTLogin(status="error", message=str(error))
    finally:
        session.commit()


def disconnect(session: Session, principal: Principal) -> None:
    setting = locked_setting(session, principal)
    if setting.chatgpt_login_ciphertext:
        try:
            active = json.loads(decrypt_secret(setting.chatgpt_login_ciphertext))
            auth_request("DELETE", "/" + quote(active["login_id"], safe=""))
        except SecretUnavailable, ModelUnavailable, ValueError, KeyError, TypeError:
            pass  # A lost Pi process or encryption key must not prevent local disconnect.
    setting.chatgpt_auth_ciphertext = setting.chatgpt_account = None
    clear_login(setting)
    if setting.kind == "chatgpt_subscription":
        setting.enabled = False
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="model_setting.chatgpt_disconnected",
        target_type="model_setting",
        target_id=str(principal.org_id),
        details={},
    )
    session.commit()


def access_token(org_id: UUID) -> str:
    with Session(engine) as session:
        setting = session.exec(
            select(ModelSetting).where(ModelSetting.org_id == org_id).with_for_update()
        ).one_or_none()
        if (
            setting is None
            or not setting.enabled
            or setting.kind != "chatgpt_subscription"
        ):
            raise ModelUnavailable("ChatGPT 订阅已关闭")
        try:
            tokens = OAuthTokens.model_validate_json(
                decrypt_secret(setting.chatgpt_auth_ciphertext or "")
            )
            if (setting.chatgpt_account or {}).get(
                "needs_relogin"
            ) or tokens.expires <= (utc_now().timestamp() + 300) * 1000:
                raise ValueError("Expired")
            # shortcut: S7b2 (NERE-104) adds serialized refresh here; until then reconnect.
            return tokens.access
        except SecretUnavailable, ValidationError, ValueError:
            needs_relogin(setting)
            session.commit()
            raise ModelUnavailable(RELOGIN) from None


def invalidate(org_id: UUID, access: str) -> None:
    with Session(engine) as session:
        setting = session.exec(
            select(ModelSetting).where(ModelSetting.org_id == org_id).with_for_update()
        ).one_or_none()
        if setting is None:
            return
        try:
            if (
                OAuthTokens.model_validate_json(
                    decrypt_secret(setting.chatgpt_auth_ciphertext or "")
                ).access
                != access
            ):
                return
        except SecretUnavailable, ValidationError:
            return
        needs_relogin(setting)
        session.commit()
