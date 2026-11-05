import http.client
import json
import re
from uuid import UUID

import sentry_sdk
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.secrets import SecretUnavailable, decrypt_secret, encrypt_secret
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.im.models import ImSetting
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
        credentials.update(updates)
        if not REQUIRED[platform] <= credentials.keys():
            raise InvalidImSetting("请填写完整的应用凭据")
        if platform == "telegram" and not re.fullmatch(
            r"[0-9]+:[A-Za-z0-9_-]+", credentials["bot_token"]
        ):
            raise InvalidImSetting("机器人令牌格式不正确")
        if updates:
            row.credentials = encrypt_secret(json.dumps(credentials))
    row.enabled = body.enabled
    row.email_auto_bind = body.email_auto_bind if platform != "telegram" else False
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


def test_connection(db: Session, org_id: UUID, platform: Platform) -> ImConnectionTest:
    if platform != "telegram":
        # shortcut: Connection tests are implemented with the platform adapters in S14/S15.
        return ImConnectionTest(
            ok=False, message="该平台的连接测试将在后续接入完成后可用"
        )
    row = db.get(ImSetting, (org_id, platform))
    if not row or not row.credentials:
        return ImConnectionTest(ok=False, message="请先保存应用凭据")
    connection = http.client.HTTPSConnection("api.telegram.org", timeout=10)
    try:
        token = json.loads(decrypt_secret(row.credentials))["bot_token"]
        # Suppress tracing because Telegram puts credentials in the URL path.
        with sentry_sdk.new_scope() as scope:
            scope.set_client(sentry_sdk.Client(dsn="", default_integrations=False))
            connection.request("GET", f"/bot{token}/getMe")
            response = connection.getresponse()
            if response.status in (401, 403, 404):
                return ImConnectionTest(ok=False, message="应用凭据无效或没有权限")
            if response.status != 200:
                return ImConnectionTest(ok=False, message="平台服务暂时不可用")
            data = json.loads(response.read(65536))
            if isinstance(data, dict) and data.get("ok") is True:
                return ImConnectionTest(ok=True, message="连接成功")
            return ImConnectionTest(ok=False, message="平台返回无效响应")
    except SecretUnavailable:
        return ImConnectionTest(ok=False, message="保存的密钥已失效，请重新填写")
    except TimeoutError:
        return ImConnectionTest(ok=False, message="连接超时，请检查网络")
    except OSError, http.client.HTTPException:
        return ImConnectionTest(ok=False, message="无法连接平台，请检查网络")
    except ValueError, KeyError:
        return ImConnectionTest(ok=False, message="平台返回无效响应")
    finally:
        connection.close()
