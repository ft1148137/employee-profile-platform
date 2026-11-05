import asyncio
import json
import re
from datetime import timedelta
from uuid import UUID

from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.secrets import SecretUnavailable, decrypt_secret, encrypt_secret
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.im.adapters.base import ImError
from app.domains.im.adapters.feishu import Feishu
from app.domains.im.adapters.telegram import Telegram
from app.domains.im.adapters.wecom import Wecom
from app.domains.im.models import ImSetting
from app.domains.im.notifications import cancel_pending
from app.domains.im.schemas import (
    ImConnectionTest,
    ImSettingPublic,
    ImSettingUpdate,
    Platform,
)
from app.domains.org.models import Organization

REQUIRED: dict[Platform, set[str]] = {
    "feishu": {"app_id", "app_secret"},
    "wecom": {"bot_id", "secret"},
    "telegram": {"bot_token"},
}


class InvalidImSetting(Exception):
    pass


def public_setting(db: Session, org_id: UUID, platform: Platform) -> ImSettingPublic:
    row = db.get(ImSetting, (org_id, platform))
    return ImSettingPublic(
        platform=platform,
        enabled=row.enabled if row else False,
        email_auto_bind=row.email_auto_bind if row else platform != "telegram",
        has_credentials=bool(row and row.credentials),
        updated_at=row.updated_at if row else None,
    )


def save_setting(
    db: Session, principal: Principal, platform: Platform, body: ImSettingUpdate
) -> ImSettingPublic:
    assert principal.user_id is not None
    allowed = REQUIRED[platform] | (
        {"corp_id", "contact_secret"} if platform == "wecom" else set()
    )
    if body.credentials.keys() - allowed:
        raise InvalidImSetting("凭据字段不适用于该平台")
    if body.enabled and not body.data_transfer_acknowledged:
        raise InvalidImSetting("请先勾选已知晓数据外发提示")
    db.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    row = db.get(ImSetting, (principal.org_id, platform)) or ImSetting(
        org_id=principal.org_id, platform=platform, updated_by_user_id=principal.user_id
    )
    updates = {
        k: v.get_secret_value()
        for k, v in body.credentials.items()
        if v.get_secret_value()
    }
    if updates or body.enabled:
        try:
            credentials = (
                json.loads(decrypt_secret(row.credentials)) if row.credentials else {}
            )
        except SecretUnavailable:
            if not REQUIRED[platform] <= updates.keys():
                raise
            credentials = {}
        previous = dict(credentials)
        credentials.update(updates)
        if not REQUIRED[platform] <= credentials.keys():
            raise InvalidImSetting("请填写完整的应用凭据")
        if platform == "telegram" and not re.fullmatch(
            r"[0-9]+:[A-Za-z0-9_-]+", credentials["bot_token"]
        ):
            raise InvalidImSetting("机器人令牌格式不正确")
        if updates and credentials != previous:
            cancel_pending(db, principal.org_id, platform)
            row.credentials = encrypt_secret(json.dumps(credentials))
            row.bot_identity = None
            row.connection_checked_at = None
    row.enabled = body.enabled
    if not row.enabled:
        row.connection_checked_at = None
        cancel_pending(db, principal.org_id, platform)
    if platform == "telegram":
        row.email_auto_bind = False
    elif body.email_auto_bind is not None:
        row.email_auto_bind = body.email_auto_bind
    row.updated_by_user_id = principal.user_id
    row.updated_at = utc_now()
    db.add(row)
    record_audit(
        db,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="im_settings.updated",
        target_type="im_setting",
        target_id=platform,
        details={
            "platform": platform,
            "fields": ["enabled", "email_auto_bind", *sorted(updates)],
        },
    )
    db.commit()
    return public_setting(db, principal.org_id, platform)


async def test_connection(
    db: Session, org_id: UUID, platform: Platform
) -> ImConnectionTest:
    row = db.get(ImSetting, (org_id, platform))
    if not row or not row.credentials:
        return ImConnectionTest(ok=False, message="请先保存应用凭据")
    if platform == "wecom" and row.enabled:
        healthy = bool(
            row.connection_checked_at
            and row.connection_checked_at > utc_now() - timedelta(seconds=150)
        )
        return ImConnectionTest(
            ok=healthy,
            message=(
                f"长连接最近心跳时间：{row.connection_checked_at.isoformat()}"
                if row.connection_checked_at
                else "尚未收到有效心跳"
            )
            + ("" if healthy else "，请检查机器人进程及凭据"),
        )
    adapter = None
    try:
        credentials = json.loads(decrypt_secret(row.credentials))
        if (
            platform == "wecom"
            and db.exec(
                select(ImSetting.org_id).where(
                    ImSetting.platform == "wecom",
                    ImSetting.enabled == True,  # noqa: E712
                    ImSetting.org_id != org_id,
                    ImSetting.bot_identity == credentials["bot_id"],
                )
            ).first()
        ):
            return ImConnectionTest(ok=False, message="该机器人已被其他企业使用")
        adapter = (
            Wecom
            if platform == "wecom"
            else Feishu
            if platform == "feishu"
            else Telegram
        )(org_id, credentials)
        async with asyncio.timeout(10):
            await adapter.test_connection()
        return ImConnectionTest(ok=True, message="连接成功")
    except TimeoutError:
        return ImConnectionTest(ok=False, message="连接超时，请检查网络")
    except SecretUnavailable:
        return ImConnectionTest(ok=False, message="保存的密钥已失效，请重新填写")
    except ImError as error:
        messages = {
            "credentials": "应用凭据无效或没有权限",
            "timeout": "连接超时，请检查网络",
            "network": "无法连接平台，请检查网络",
            "invalid_response": "平台返回无效响应",
        }
        return ImConnectionTest(
            ok=False, message=messages.get(error.code, "平台服务暂时不可用")
        )
    except ValueError, KeyError, TypeError:
        return ImConnectionTest(ok=False, message="平台返回无效响应")
    finally:
        if adapter:
            await adapter.close()
