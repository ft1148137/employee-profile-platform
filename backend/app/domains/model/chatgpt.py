import json
import os
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import httpx
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.config import settings
from app.core.db import engine
from app.core.secrets import decrypt_secret, encrypt_secret
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.model.models import ModelSetting
from app.domains.model.schemas import ChatGPTAccount, ChatGPTLogin
from app.domains.org.models import Organization

TOKEN_DIR = Path(tempfile.mkdtemp(prefix="epp-chatgpt-"))
TOKEN_FILE = TOKEN_DIR / "auth.json"
CALL_LOCK = threading.Lock()
RELOGIN = "ChatGPT 登录已失效，请在设置页重新连接"


def sdk_auth() -> Any:
    from app.domains.model.client import _sdk

    _sdk()
    # shortcut: LiteLLM 1.106.6 internals; verify signatures on every upgrade.
    from litellm.llms.chatgpt import common_utils
    from litellm.llms.chatgpt.authenticator import Authenticator

    return Authenticator(), common_utils


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


def login(
    session: Session, principal: Principal, *, start: bool = False
) -> ChatGPTLogin:
    setting = locked_setting(session, principal)
    auth, constants = sdk_auth()
    now = utc_now()
    active = (
        setting.chatgpt_login_ciphertext
        and setting.chatgpt_login_expires_at
        and setting.chatgpt_login_expires_at > now
    )
    if not active and start:
        try:
            code = auth._request_device_code()
            code["interval"] = str(max(5, int(code.get("interval", 5))))
        except constants.GetDeviceCodeError, ValueError:
            session.rollback()
            return ChatGPTLogin(
                status="error", message="无法开始 ChatGPT 登录，请稍后重试"
            )
        setting.chatgpt_login_ciphertext = encrypt_secret(json.dumps(code))
        setting.chatgpt_login_expires_at = now + timedelta(minutes=15)
        setting.chatgpt_login_polled_at = None
        active = True
    if not active:
        status: Literal["expired", "none", "connected"] = (
            "expired" if setting.chatgpt_login_expires_at else "none"
        )
        if setting.chatgpt_auth_ciphertext and not setting.chatgpt_login_expires_at:
            status = "connected"
        result = ChatGPTLogin(
            status=status,
            account=ChatGPTAccount(**setting.chatgpt_account)
            if setting.chatgpt_account
            else None,
        )
        session.commit()
        return result
    assert setting.chatgpt_login_ciphertext
    code = json.loads(decrypt_secret(setting.chatgpt_login_ciphertext))
    result = ChatGPTLogin(
        status="pending",
        verification_url=constants.CHATGPT_DEVICE_VERIFY_URL,
        user_code=code["user_code"],
        expires_at=setting.chatgpt_login_expires_at,
    )
    if not start and (
        not setting.chatgpt_login_polled_at
        or (now - setting.chatgpt_login_polled_at).total_seconds()
        >= int(code["interval"])
    ):
        setting.chatgpt_login_polled_at = now
        try:
            # One bounded poll, never LiteLLM's blocking 15-minute loop.
            with httpx.Client(timeout=10) as client:
                response = client.post(
                    constants.CHATGPT_DEVICE_TOKEN_URL,
                    json={k: code[k] for k in ("device_auth_id", "user_code")},
                )
            if response.status_code not in (403, 404):
                response.raise_for_status()
                data = response.json()
                if not all(
                    isinstance(data.get(k), str) and data[k]
                    for k in ("authorization_code", "code_challenge", "code_verifier")
                ):
                    raise ValueError("Invalid authorization response")
                record = auth._build_auth_record(auth._exchange_code_for_tokens(data))
                claims = auth._decode_jwt_claims(record["id_token"])
                auth_claims = claims.get("https://api.openai.com/auth")
                account = {
                    "email": claims.get("email"),
                    "plan_type": auth_claims.get("chatgpt_plan_type")
                    if isinstance(auth_claims, dict)
                    else None,
                    "connected_at": now.isoformat(),
                    "needs_relogin": False,
                }
                setting.chatgpt_auth_ciphertext = encrypt_secret(json.dumps(record))
                setting.chatgpt_account = account
                setting.chatgpt_login_ciphertext = None
                setting.chatgpt_login_expires_at = setting.chatgpt_login_polled_at = (
                    None
                )
                record_audit(
                    session,
                    org_id=principal.org_id,
                    actor_type=principal.actor_type,
                    actor_id=str(principal.user_id),
                    action="model_setting.chatgpt_connected",
                    target_type="model_setting",
                    target_id=str(principal.org_id),
                    details={"plan_type": account["plan_type"]},
                )
                result = ChatGPTLogin(
                    status="connected", account=ChatGPTAccount(**account)
                )
        except httpx.HTTPError, ValueError, constants.GetAccessTokenError:
            result.status = "error"
            result.message = "ChatGPT 登录查询失败，请稍后重试"
    session.commit()
    return result


def disconnect(session: Session, principal: Principal) -> None:
    setting = locked_setting(session, principal)
    setting.chatgpt_auth_ciphertext = setting.chatgpt_account = None
    setting.chatgpt_login_ciphertext = None
    setting.chatgpt_login_expires_at = setting.chatgpt_login_polled_at = None
    if setting.kind == "chatgpt_subscription":
        setting.enabled = False
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="model_setting.chatgpt_disconnected",
        details={},
        target_type="model_setting",
        target_id=str(principal.org_id),
    )
    session.commit()


def local_auth() -> dict[str, Any]:
    from app.domains.model.client import ModelUnavailable

    assert settings.CHATGPT_CODEX_AUTH_FILE
    try:
        tokens = json.loads(settings.CHATGPT_CODEX_AUTH_FILE.read_text())["tokens"]
        auth, _ = sdk_auth()
        expires = auth._get_expires_at(tokens["access_token"])
        if not expires or expires - utc_now().timestamp() < 3600:
            raise ModelUnavailable(
                "本机 Codex 登录即将过期：在本机运行一次 codex 让它刷新，然后执行 docker compose up -d --force-recreate backend worker"
            )
        return {
            "access_token": tokens["access_token"],
            "id_token": tokens["id_token"],
            "account_id": tokens.get("account_id")
            or auth._extract_account_id(tokens["id_token"]),
            "expires_at": expires,
        }
    except OSError, KeyError, TypeError, ValueError:
        raise ModelUnavailable("无法读取本机 Codex 登录，请在本机重新登录") from None


def completion(org_id: UUID | None, call: Callable[[], Any]) -> Any:
    from app.domains.model.client import ModelUnavailable

    if org_id is None:
        raise ModelUnavailable(RELOGIN)
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
        with CALL_LOCK:
            local = settings.CHATGPT_CODEX_AUTH_FILE is not None
            if local:
                record = json.dumps(local_auth())
            elif setting.chatgpt_auth_ciphertext and not (
                setting.chatgpt_account or {}
            ).get("needs_relogin"):
                record = decrypt_secret(setting.chatgpt_auth_ciphertext)
            else:
                raise ModelUnavailable(RELOGIN)
            auth, constants = sdk_auth()
            try:
                with open(
                    TOKEN_FILE,
                    "w",
                    opener=lambda path, flags: os.open(path, flags, 0o600),
                ) as file:
                    file.write(record)
                # Never let the SDK start interactive login, including CLI/main-thread callers.
                with ThreadPoolExecutor(max_workers=1) as executor:
                    return executor.submit(call).result()
            except Exception as error:
                if (
                    isinstance(error, constants.GetAccessTokenError)
                    or getattr(error, "status_code", None) == 401
                ):
                    if not local:
                        setting.chatgpt_account = {
                            **(setting.chatgpt_account or {}),
                            "needs_relogin": True,
                        }
                    raise ModelUnavailable(RELOGIN) from None
                raise
            finally:
                try:
                    if not local and TOKEN_FILE.exists():
                        updated = TOKEN_FILE.read_text()
                        if updated != record:
                            setting.chatgpt_auth_ciphertext = encrypt_secret(updated)
                    session.commit()
                finally:
                    TOKEN_FILE.unlink(missing_ok=True)
