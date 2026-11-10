"""Connection changes reuse manual services; credentials never enter previews."""

import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, SecretStr
from sqlmodel import Session

from app.authz.principal import Principal
from app.core.secrets import SecretUnavailable, decrypt_secret
from app.domains.access_token import service as mcp
from app.domains.access_token.schemas import McpSettingUpdate
from app.domains.drafting.service import active_employees
from app.domains.im import settings as im
from app.domains.im.models import ImSetting
from app.domains.im.schemas import ImSettingUpdate

if TYPE_CHECKING:
    from app.domains.admin_assistant.registry import AdminFunction, ConnectionTestResult


class FeishuArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["feishu"] = Field(
        default="feishu", description="仅支持飞书；企业微信和 Telegram 请到网页上修改"
    )
    enabled: bool = False
    email_auto_bind: bool = True
    app_id: SecretStr | None = Field(default=None, max_length=4096)
    app_secret: SecretStr | None = Field(default=None, max_length=4096)


def im_snapshot(db: Session, p: Principal, _: Any) -> BaseModel:
    row = im.public_setting(db, p.org_id, "feishu")
    return FeishuArgs(enabled=row.enabled, email_auto_bind=row.email_auto_bind)


def save_im(
    db: Session, p: Principal, args: FeishuArgs, *, commit: bool = True
) -> BaseModel:
    return im.save_setting(
        db,
        p,
        args.platform,
        ImSettingUpdate(
            enabled=args.enabled,
            email_auto_bind=args.email_auto_bind,
            credentials={
                field: value
                for field in ("app_id", "app_secret")
                if (value := getattr(args, field)) is not None
            },
            # Only the confirmed card invokes this handler; enabling needs two clicks.
            data_transfer_acknowledged=args.enabled,
        ),
        commit=commit,
    )


async def test_im(db: Session, p: Principal, args: FeishuArgs) -> ConnectionTestResult:
    from app.domains.admin_assistant.registry import ConnectionTestResult

    row = db.get(ImSetting, (p.org_id, "feishu"))
    try:
        credentials = (
            json.loads(decrypt_secret(row.credentials))
            if row and row.credentials
            else {}
        )
    except SecretUnavailable:
        if not (args.app_id and args.app_secret):
            raise
        credentials = {}
    credentials.update(
        {
            field: value.get_secret_value()
            for field in ("app_id", "app_secret")
            if (value := getattr(args, field)) is not None and value.get_secret_value()
        }
    )
    if not im.REQUIRED["feishu"] <= credentials.keys():
        return ConnectionTestResult(ok=False, message="请先填写完整的应用凭据")
    result = await im.test_credentials(db, p.org_id, "feishu", credentials)
    return ConnectionTestResult(
        ok=result.ok,
        message="连接成功" if result.ok else "飞书连接失败，请检查应用凭据、权限和网络",
    )


def register_connections(functions: dict[str, AdminFunction]) -> None:
    from app.domains.admin_assistant.registry import AdminFunction

    functions["update_im_setting"] = AdminFunction(
        name="update_im_setting",
        area="connections",
        kind="change",
        risk="normal",
        preview_risk=lambda d, p, a: (
            "high"
            if a.enabled and not im.public_setting(d, p.org_id, "feishu").enabled
            else "normal"
        ),
        description="修改飞书入口；企业微信和 Telegram 请到网页上改；凭据只用安全输入框填写",
        input_model=FeishuArgs,
        handler=save_im,
        page="/admin/connections?tab=im",
        endpoints=(("PUT", "/im-settings/{platform}"),),
        snapshot=im_snapshot,
        merge_settings=True,
        secret_fields=("app_id", "app_secret"),
        test=test_im,
        impact=lambda d, p, a: RootModel(
            {
                "count": len(active_employees(d, p.org_id)),
                "text": "打开入口后，员工可绑定账号并使用机器人，消息会经过飞书。IM 里的消息会存在该平台的服务器上；再次确认即表示已知晓数据外发提示。撤销只恢复开关和邮箱自动绑定，凭据不回滚",
            }
        ),
    )
    functions["update_mcp_setting"] = AdminFunction(
        name="update_mcp_setting",
        area="connections",
        kind="change",
        risk="normal",
        preview_risk=lambda d, p, a: (
            "high" if a.enabled and not mcp.get_setting(d, p).enabled else "normal"
        ),
        description="修改外部 AI 助手开关和允许的客户端；开启需再次确认数据外发",
        input_model=McpSettingUpdate,
        merge_settings=True,
        handler=mcp.save_setting,
        page="/admin/connections?tab=mcp",
        endpoints=(("PUT", "/mcp-setting"),),
        snapshot=lambda d, p, a: McpSettingUpdate.model_validate(
            mcp.get_setting(d, p).model_dump(include=set(McpSettingUpdate.model_fields))
        ),
        impact=lambda d, p, a: RootModel(
            {
                "count": len(active_employees(d, p.org_id)),
                "text": "打开后，外部助手可读写员工本人的数据，画像、证据和自述会发给外部助手的厂商；权限不超过员工本人",
            }
        ),
    )
