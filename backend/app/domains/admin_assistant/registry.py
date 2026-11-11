"""Administrative reads shared with the manual console; identity is server-owned."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    ValidationError,
)
from sqlmodel import Session

from app.authz.deps import get_org_admin
from app.authz.principal import Principal
from app.domains.access_token import service as access
from app.domains.account import service as accounts
from app.domains.account.schemas import AccountStatus
from app.domains.admin.overview import build_overview
from app.domains.admin_assistant import model_settings
from app.domains.admin_assistant.connections import register_connections
from app.domains.admin_assistant.files import register_files
from app.domains.admin_assistant.people import register_people
from app.domains.admin_assistant.rules import register_rules
from app.domains.admin_assistant.sources import register_sources
from app.domains.audit import service as audit
from app.domains.base import utc_now
from app.domains.bot import settings as bot
from app.domains.capability.service import read_editor
from app.domains.drafting import settings as drafting
from app.domains.drafting import update_settings as updates
from app.domains.drafting.service import DraftProgress, active_employees, read_progress
from app.domains.im import settings as im
from app.domains.im.schemas import Platform
from app.domains.model import service as model
from app.domains.org.employee_fields import EmployeeFields, save_employee_fields
from app.domains.org.models import EmploymentStatus, Organization
from app.domains.org.roster import service as roster
from app.domains.org.roster import single
from app.domains.org.service import visible_employee
from app.domains.profile.service import read_profile
from app.domains.review import appeal
from app.domains.review.policy import PolicyInput, policy_view, read_policy, save_policy
from app.domains.setup.wizard import read_setup
from app.domains.source import identities
from app.domains.source import service as sources
from app.domains.source.schemas import SyncRunsPublic

Area = Literal["home", "people", "rules", "data_ai", "connections", "more"]
Handler = Callable[..., BaseModel | Awaitable[BaseModel]]
Snapshot = Callable[[Session, Principal, Any], BaseModel]
Reverse = Callable[[Session, Principal, dict[str, JsonValue]], tuple[str, BaseModel]]
Endpoint = tuple[str, str]
Risk = Literal["normal", "high"]


class ConnectionTestResult(BaseModel):
    ok: bool
    message: str


ConnectionTest = Callable[
    [Session, Principal, Any], ConnectionTestResult | Awaitable[ConnectionTestResult]
]
Rebinds = Callable[[dict[str, JsonValue], dict[str, JsonValue]], bool]


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageArgs(NoArgs):
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=100)


class ProgressArgs(PageArgs):
    kind: Literal["jira", "csv"] | None = None


class EmployeeFilter(NoArgs):
    employee_id: UUID | None = None


class EmployeeArgs(NoArgs):
    employee_id: UUID


class AppealArgs(NoArgs):
    appeal_id: UUID


class AccountArgs(PageArgs):
    department_id: UUID | None = None
    status: AccountStatus | None = None
    q: str | None = None
    employment_status: EmploymentStatus | None = None
    employee_id: UUID | None = None


class IdentityArgs(PageArgs):
    limit: int = Field(default=50, ge=1, le=200)
    status: Literal["unlinked", "linked", "all"] = "unlinked"
    source: str | None = None
    q: str | None = None
    employee_id: UUID | None = None


class AuditArgs(PageArgs):
    limit: int = Field(default=50, ge=1, le=100)
    action: str | None = None
    actor_user_id: UUID | None = None
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    target_type: str | None = None
    target_id: list[str] | None = Field(default=None, max_length=20)
    employee_id: UUID | None = None


class ImArgs(NoArgs):
    platform: Platform


class ProgressPublic(BaseModel):
    sync_runs: SyncRunsPublic
    drafting: DraftProgress


class TableCard(BaseModel):
    queried_at: AwareDatetime | None = None
    type: Literal["table"] = "table"
    title: str
    columns: list[str]
    rows: list[list[JsonValue]] = Field(max_length=20)
    total: int
    page: str


@dataclass(frozen=True)
class AdminFunction:
    name: str
    area: Area
    kind: Literal["read", "change"]
    risk: Risk | Callable[[Any], Risk]
    description: str
    input_model: type[BaseModel]
    handler: Handler
    page: str
    endpoints: tuple[Endpoint, ...]
    snapshot: Snapshot | None = None
    impact: Snapshot | None = None
    secret_fields: tuple[str, ...] = ()
    file_field: str | None = None
    reverse: Reverse | None = None
    undoable: bool = True
    preview_risk: Callable[[Session, Principal, Any], Risk] | None = None
    test: ConnectionTest | None = None
    rebinds: Rebinds | None = None
    merge_settings: bool = False

    @property
    def private_fields(self) -> set[str]:
        return set(self.secret_fields) | (
            {self.file_field} if self.file_field else set()
        )


def _profile(db: Session, principal: Principal, args: EmployeeArgs) -> BaseModel:
    result = read_profile(db, principal, args.employee_id).model_copy(deep=True)
    titles = {
        "work_record": "工作记录",
        "self_report": "员工自述",
        "manager_review": "经理评价",
    }
    for item in result.items:
        if item.claim:
            for ref in item.claim.evidence:
                ref.title = titles[ref.kind]
    return result


def _audit(db: Session, principal: Principal, args: AuditArgs) -> BaseModel:
    if args.employee_id is not None:
        visible_employee(db, principal, args.employee_id)
    values = args.model_dump()
    values["target_ids"] = values.pop("target_id")
    return audit.list_events(db, principal.org_id, **values)


async def _im_test(db: Session, principal: Principal, args: ImArgs) -> BaseModel:
    return await im.test_connection(db, principal.org_id, args.platform)


def _f(
    name: str,
    area: Area,
    description: str,
    input_model: type[BaseModel],
    handler: Handler,
    page: str,
    *endpoints: str,
) -> AdminFunction:
    return AdminFunction(
        name,
        area,
        "read",
        "normal",
        description,
        input_model,
        handler,
        page,
        tuple((e.split(" ", 1)[0], e.split(" ", 1)[1]) for e in endpoints),
    )


FUNCTIONS = {
    f.name: f
    for f in [
        _f(
            "get_today",
            "home",
            "查看今天要处理的事和运行状态",
            NoArgs,
            lambda d, p, a: build_overview(d, p),
            "/admin",
            "GET /admin/overview",
        ),
        _f(
            "get_progress",
            "home",
            "查看同步记录和画像起草进度",
            ProgressArgs,
            lambda d, p, a: ProgressPublic(
                sync_runs=sources.read_sync_runs(d, p, **a.model_dump()),
                drafting=read_progress(d, p.org_id),
            ),
            "/admin",
            "GET /sync-runs",
            "GET /initial-profiles/progress",
        ),
        _f(
            "get_setup_status",
            "home",
            "查看首次设置完成情况",
            NoArgs,
            lambda d, p, a: read_setup(d, p.org_id),
            "/admin/setup",
            "GET /setup",
        ),
        _f(
            "list_accounts",
            "people",
            "查看员工及账号状态",
            AccountArgs,
            lambda d, p, a: accounts.list_accounts(d, p, **a.model_dump()),
            "/admin/people?tab=accounts",
            "GET /accounts",
        ),
        _f(
            "list_roster_imports",
            "people",
            "查看名单导入记录",
            PageArgs,
            lambda d, p, a: roster.list_imports(d, p, **a.model_dump()),
            "/admin/people?tab=roster",
            "GET /roster/imports",
        ),
        _f(
            "list_roles",
            "people",
            "查看管理员和经理角色",
            NoArgs,
            lambda d, p, a: RootModel(accounts.list_roles(d, p)),
            "/admin/people?tab=roles",
            "GET /role-grants",
        ),
        _f(
            "list_appeals",
            "people",
            "查看待处理的争议",
            EmployeeFilter,
            lambda d, p, a: RootModel(appeal.list_disputes(p, d, **a.model_dump())),
            "/admin/people?tab=appeals",
            "GET /appeals",
        ),
        _f(
            "get_appeal",
            "people",
            "查看一个争议的处理情况",
            AppealArgs,
            lambda d, p, a: appeal.read_appeal(p, d, a.appeal_id),
            "/admin/people?tab=appeals",
            "GET /appeals/{appeal_id}",
        ),
        _f(
            "get_employee_profile",
            "people",
            "查看可见员工的正式画像",
            EmployeeArgs,
            _profile,
            "/employees",
            "GET /employees/{employee_id}/profile",
        ),
        _f(
            "get_capability_dictionary",
            "rules",
            "查看当前能力词典",
            NoArgs,
            lambda d, p, a: read_editor(d, p),
            "/admin/rules?tab=dictionary",
            "GET /capabilities/editor",
        ),
        _f(
            "get_approval_policy",
            "rules",
            "查看经理确认规则",
            NoArgs,
            lambda d, p, a: policy_view(d, p.org_id),
            "/admin/rules?tab=approval",
            "GET /approval-policy",
        ),
        _f(
            "get_drafting_settings",
            "data_ai",
            "查看企业自动起草总开关",
            NoArgs,
            lambda d, p, a: drafting.read_settings(d, p.org_id),
            "/admin/data-ai?tab=model",
            "GET /drafting-settings",
        ),
        _f(
            "get_update_settings",
            "rules",
            "查看定期提醒设置",
            NoArgs,
            lambda d, p, a: updates.read_update_settings(d, p.org_id),
            "/admin/rules?tab=reminders",
            "GET /update-settings",
        ),
        _f(
            "get_sources",
            "data_ai",
            "查看数据来源配置（不含凭据）",
            NoArgs,
            lambda d, p, a: sources.public_jira(sources.read_jira(d, p.org_id)),
            "/admin/data-ai?tab=sources",
            "GET /sources/jira",
        ),
        _f(
            "list_identities",
            "data_ai",
            "查看 Jira 账号匹配情况",
            IdentityArgs,
            lambda d, p, a: identities.list_identities(d, p.org_id, **a.model_dump()),
            "/admin/people?tab=identities",
            "GET /external-identities",
        ),
        _f(
            "get_model_setting",
            "data_ai",
            "查看模型设置（不含密钥）",
            NoArgs,
            lambda d, p, a: model.public_setting(model.read_setting(d, p.org_id)),
            "/admin/data-ai?tab=model",
            "GET /model-setting",
        ),
        _f(
            "get_bot_settings",
            "data_ai",
            "查看内置机器人设置",
            NoArgs,
            lambda d, p, a: bot.read_settings(d, p.org_id),
            "/admin/data-ai?tab=bot",
            "GET /bot-settings",
        ),
        _f(
            "get_im_settings",
            "connections",
            "查看消息平台连接设置（不含凭据）",
            NoArgs,
            lambda d, p, a: RootModel(im.list_settings(d, p.org_id)),
            "/admin/connections?tab=im",
            "GET /im-settings",
        ),
        _f(
            "get_mcp_setting",
            "connections",
            "查看外部 AI 助手开关",
            NoArgs,
            lambda d, p, a: access.get_setting(d, p),
            "/admin/connections?tab=mcp",
            "GET /mcp-setting",
        ),
        _f(
            "test_model",
            "data_ai",
            "测试已保存的模型连接",
            NoArgs,
            lambda d, p, a: model.check_connection(d, p),
            "/admin/data-ai?tab=model",
            "POST /model-setting/test",
        ),
        _f(
            "test_jira_connection",
            "data_ai",
            "测试已保存的 Jira 连接",
            NoArgs,
            lambda d, p, a: sources.check_jira(d, p),
            "/admin/data-ai?tab=sources",
            "POST /sources/jira/test",
        ),
        _f(
            "test_im_connection",
            "connections",
            "测试已保存的消息平台连接",
            ImArgs,
            _im_test,
            "/admin/connections?tab=im",
            "POST /im-settings/{platform}/test",
        ),
        _f(
            "list_audit_events",
            "more",
            "查看操作审计记录",
            AuditArgs,
            _audit,
            "/admin/audit",
            "GET /audit-events",
        ),
    ]
}

FUNCTIONS["update_employee_fields"] = AdminFunction(
    name="update_employee_fields",
    area="people",
    kind="change",
    risk="normal",
    description="开启或关闭员工档案固定字段，不进入画像或 AI；关闭保留已有值",
    input_model=EmployeeFields,
    handler=save_employee_fields,
    page="/admin/people?tab=roster",
    endpoints=(("PUT", "/employee-fields"),),
    snapshot=lambda d, p, a: EmployeeFields(
        fields=d.get_one(Organization, p.org_id).employee_fields
    ),
    impact=lambda d, p, a: RootModel(
        {
            "count": len(active_employees(d, p.org_id)),
            "text": "变更员工档案字段的显示及名单导入；已有预览失效",
        }
    ),
)

FUNCTIONS["update_update_settings"] = AdminFunction(
    name="update_update_settings",
    area="rules",
    kind="change",
    risk="normal",
    description="修改定期提醒周期和证据较旧期限",
    input_model=updates.UpdateSettingsInput,
    handler=updates.save_update_settings,
    page="/admin/rules?tab=reminders",
    endpoints=(("PUT", "/update-settings"),),
    merge_settings=True,
    snapshot=lambda d, p, a: updates.UpdateSettingsInput.model_validate(
        updates.read_update_settings(d, p.org_id).model_dump(
            include=set(updates.UpdateSettingsInput.model_fields)
        )
    ),
    impact=lambda d, p, a: RootModel(
        {
            "count": len(active_employees(d, p.org_id)),
            "text": "影响在职员工的定期提醒和证据期限",
        }
    ),
)


FUNCTIONS["update_approval_policy"] = AdminFunction(
    name="update_approval_policy",
    area="rules",
    kind="change",
    risk=lambda a: (
        "high"
        if not a.require_manager or False in a.category_overrides.values()
        else "normal"
    ),
    description="修改经理确认规则，关闭全部或任一能力类别的经理确认需要再次确认",
    input_model=PolicyInput,
    handler=save_policy,
    page="/admin/rules?tab=approval",
    endpoints=(("PUT", "/approval-policy"),),
    merge_settings=True,
    snapshot=lambda d, p, a: PolicyInput.model_validate(
        read_policy(d, p.org_id).model_dump(include=set(PolicyInput.model_fields))
    ),
    impact=lambda d, p, a: RootModel(
        {
            "count": len(active_employees(d, p.org_id)),
            "text": "影响全体在职员工的画像发布：关闭的类别由本人确认后发布为暂定，无需经理确认",
        }
    ),
)


FUNCTIONS["update_model_setting"] = AdminFunction(
    name="update_model_setting",
    area="data_ai",
    kind="change",
    risk="normal",
    description="修改模型设置；换模型、换服务商、关闭模型需要再次确认；密钥只用安全输入框填写",
    input_model=model_settings.ModelArgs,
    handler=model.save_setting,
    page="/admin/data-ai?tab=model",
    endpoints=(("PUT", "/model-setting"),),
    merge_settings=True,
    snapshot=model_settings.snapshot,
    impact=model_settings.impact,
    secret_fields=("api_key",),
    preview_risk=model_settings.risk,
    test=model_settings.test_model,
    rebinds=model_settings.rebinds,
)

FUNCTIONS["update_bot_settings"] = AdminFunction(
    name="update_bot_settings",
    area="data_ai",
    kind="change",
    risk="normal",
    description="修改内置机器人的提问指南和聊天原文保留天数",
    input_model=bot.BotSettings,
    handler=bot.save_settings,
    page="/admin/data-ai?tab=bot",
    endpoints=(("PUT", "/bot-settings"),),
    merge_settings=True,
    snapshot=lambda d, p, a: bot.read_settings(d, p.org_id),
    preview_risk=lambda d, p, a: (
        "high"
        if a.retention_days < bot.read_settings(d, p.org_id).retention_days
        else "normal"
    ),
    impact=lambda d, p, a: RootModel(
        {
            "count": len(active_employees(d, p.org_id)),
            "text": (
                f"会删除超过 {a.retention_days} 天的对话记录，删除后不能恢复"
                + ("；0 天表示闲置 2 小时后清除" if a.retention_days == 0 else "")
                if a.retention_days < bot.read_settings(d, p.org_id).retention_days
                else "更新提问指南和对话保留时间"
            )
            + "；撤销不会恢复已删除的记录",
        }
    ),
)


def list_admin_functions(area: Area) -> dict[str, Any]:
    return {
        "data": [
            {
                "name": f.name,
                "description": f.description,
                "kind": f.kind,
                "risk": "按改动内容判断"
                if callable(f.risk) or f.preview_risk
                else f.risk,
                "input_model": f.input_model.model_json_schema(),
                "secret_fields": list(f.secret_fields),
            }
            for f in FUNCTIONS.values()
            if f.area == area
        ]
    }


def _table(function: AdminFunction, data: Any) -> TableCard | None:
    if function.name == "get_progress":
        data = data["sync_runs"]
    rows = (
        data
        if isinstance(data, list)
        else data.get("data")
        if isinstance(data, dict)
        else None
    )
    if not isinstance(rows, list):
        return None
    columns = list(rows[0]) if rows else []
    return TableCard(
        queried_at=utc_now(),
        title=function.description,
        columns=columns,
        rows=[[row.get(c) for c in columns] for row in rows[:20]],
        total=len(rows) if isinstance(data, list) else data.get("count", len(rows)),
        page=function.page,
    )


async def admin_read(
    db: Session, principal: Principal, function: str, args: dict[str, Any]
) -> dict[str, Any]:
    try:
        get_org_admin(principal)
        entry = FUNCTIONS.get(function)
        if entry is None or entry.kind != "read":
            return {"data": {"error": "该功能不存在或不是只读功能"}}
        validated = entry.input_model.model_validate(args)
        result = entry.handler(db, principal, validated)
        if isawaitable(result):
            result = await result
        data = result.model_dump(
            mode="json",
            exclude_unset=function == "get_sources",
            exclude={"data": {"__all__": {"job_family", "work_location", "hire_date"}}}
            if function == "list_accounts"
            else {"employee": {"job_family", "work_location", "hire_date"}}
            if function == "get_employee_profile"
            else None,
        )
        card = _table(entry, data)
        return {"data": data, "cards": [card.model_dump(mode="json")] if card else []}
    except ValidationError:
        return {"data": {"error": "参数不正确，请按功能说明填写，不能指定身份或企业"}}
    except HTTPException as error:
        # Never echo arbitrary service details (including rejected input) to the model.
        message = (
            "找不到该记录"
            if error.status_code == 404
            else "需要企业管理员权限"
            if error.status_code == 403
            else "查询失败，请在对应页面检查设置"
        )
        return {"data": {"error": message}}


NOT_FOR_ASSISTANT = {
    (
        "POST",
        "/admin-assistant/change-sets/{id}/test",
    ): "由创建人在确认卡上测试草稿连接",
    (
        "GET",
        "/admin-assistant/change-sets",
    ): "手工页面读取本人当前会话的草稿，不作为模型工具",
    (
        "PUT",
        "/admin-assistant/change-sets/{id}/secrets/{field}",
    ): "密钥由创建人通过安全输入框直接提交",
    ("GET", "/admin-assistant/change-sets/{id}"): "变更集只能由创建人在确认卡上操作",
    (
        "POST",
        "/admin-assistant/change-sets/{id}/apply",
    ): "变更集只能由创建人在确认卡上操作",
    (
        "POST",
        "/admin-assistant/change-sets/{id}/discard",
    ): "变更集只能由创建人在确认卡上操作",
    (
        "POST",
        "/admin-assistant/change-sets/{id}/undo",
    ): "变更集只能由创建人在确认卡上操作",
    ("GET", "/admin-assistant/sessions"): "助手自身的会话接口",
    ("POST", "/admin-assistant/sessions"): "助手自身的会话接口",
    ("GET", "/admin-assistant/sessions/{session_id}/messages"): "助手自身的会话接口",
    ("POST", "/admin-assistant/sessions/{session_id}/turns"): "助手自身的会话接口",
    (
        "POST",
        "/admin-assistant/sessions/{session_id}/files",
    ): "管理员通过上传框提交文件，不由模型传入内容",
    (
        "POST",
        "/admin-assistant/sessions/{session_id}/attachments",
    ): "本人上传对话附件，模型不能上传",
    (
        "GET",
        "/admin-assistant/sessions/{session_id}/attachments/{attachment_id}",
    ): "本人读取附件原文，不向模型提供下载工具",
    ("GET", "/admin-assistant/availability"): "助手自身的会话接口",
    ("GET", "/sources/csv/template"): "模板下载由助手给页面链接",
    ("GET", "/roster/template"): "模板下载由助手给页面链接",
    ("GET", "/capabilities/template"): "模板下载由助手给页面链接",
    ("GET", "/model-setting/chatgpt/login"): "在模型页面操作",
    ("POST", "/model-setting/chatgpt/login"): "在模型页面操作",
    ("POST", "/model-setting/chatgpt/callback"): "在模型页面操作",
    ("DELETE", "/model-setting/chatgpt/login"): "在模型页面操作",
    ("DELETE", "/model-setting/chatgpt"): "在模型页面操作",
}

register_rules(FUNCTIONS)
register_sources(FUNCTIONS)
register_connections(FUNCTIONS)
register_files(FUNCTIONS)
register_people(FUNCTIONS)

for name, title, input_type in (
    ("onboard_employee", "新增员工", single.Onboard),
    ("transfer_employee", "员工调岗", single.Transfer),
    ("offboard_employees", "办理离职", single.Offboard),
):
    FUNCTIONS[name] = AdminFunction(
        name=name,
        area="people",
        kind="change",
        risk=lambda a: (
            "high"
            if isinstance(a, single.Offboard) and len(set(a.employee_ids)) >= 2
            else "normal"
        ),
        description=title,
        input_model=input_type,
        handler=single.apply,
        page="/admin/people",
        # All three operations share a discriminated route, classified once.
        endpoints=(("POST", "/roster/single-change"),)
        if name == "onboard_employee"
        else (),
        snapshot=single.snapshot,
        impact=single.impact,
        undoable=False,
    )

PENDING: dict[Endpoint, str] = {}
