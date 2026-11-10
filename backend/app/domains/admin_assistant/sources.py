"""Source actions use the same services and credential binding as the manual console."""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, JsonValue, RootModel
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.secrets import decrypt_secret
from app.domains.org.models import ExternalIdentity
from app.domains.source import identities, service, sync
from app.domains.source.schemas import IdentityLinks, JiraConfig, JiraConnectionUpdate

if TYPE_CHECKING:
    from app.domains.admin_assistant.registry import AdminFunction, ConnectionTestResult


class JiraArgs(JiraConnectionUpdate):
    model_config = ConfigDict(extra="forbid")


class IdentityArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity_id: UUID
    employee_id: UUID | None = None


class LinkArgs(IdentityArgs):
    employee_id: UUID


class RetryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID


def jira_snapshot(db: Session, p: Principal, _: Any) -> BaseModel:
    row = service.read_jira(db, p.org_id)
    return RootModel(row.config | {"sync_schedule": row.sync_schedule} if row else None)


def jira_rebinds(current: dict[str, JsonValue], target: dict[str, JsonValue]) -> bool:
    return any(
        current.get(k) != target.get(k) for k in ("deployment", "auth_type", "base_url")
    )


def test_jira(db: Session, p: Principal, args: JiraArgs) -> ConnectionTestResult:
    from app.domains.admin_assistant.registry import ConnectionTestResult

    row = service.read_jira(db, p.org_id)
    if args.secret is None:
        if (
            not row
            or jira_rebinds(row.config, args.model_dump(mode="json"))
            or not row.secret_ciphertext
        ):
            return ConnectionTestResult(ok=False, message="请先填写新的凭据")
        secret = decrypt_secret(row.secret_ciphertext)
    else:
        secret = args.secret.get_secret_value()
    result = service.test_jira(JiraConfig.model_validate(args.model_dump()), secret)
    # Never pass an upstream response, account name or exception text to the assistant.
    return ConnectionTestResult(
        ok=result.ok,
        message="连接成功"
        if result.ok
        else "Jira 连接失败，请检查凭据、站点地址和网络",
    )


def identity_snapshot(db: Session, p: Principal, args: IdentityArgs) -> BaseModel:
    row = db.exec(
        select(ExternalIdentity).where(
            ExternalIdentity.org_id == p.org_id, ExternalIdentity.id == args.identity_id
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "账号不存在")
    return IdentityArgs(identity_id=row.id, employee_id=row.employee_id)


def restore_identity(
    _db: Session, _p: Principal, before: dict[str, JsonValue]
) -> tuple[str, BaseModel]:
    return (
        "link_identity" if before["employee_id"] else "unlink_identity",
        IdentityArgs.model_validate(before),
    )


def link_identity(
    db: Session, p: Principal, args: IdentityArgs, *, commit: bool = True
) -> BaseModel:
    return identities.link_identities(
        db,
        p,
        IdentityLinks.model_validate({"links": [args.model_dump()]}),
        commit=commit,
    )


def register_sources(functions: dict[str, AdminFunction]) -> None:
    from app.domains.admin_assistant.registry import AdminFunction, NoArgs

    for entry in [
        AdminFunction(
            "update_jira_connection",
            "data_ai",
            "change",
            "normal",
            "修改 Jira 连接和同步周期；凭据只用安全输入框填写",
            JiraArgs,
            service.save_jira,
            "/admin/data-ai?tab=sources",
            (("PUT", "/sources/jira"),),
            snapshot=jira_snapshot,
            impact=lambda d, p, a: RootModel(
                {
                    "count": 0,
                    "text": "更新工作记录来源；撤销只恢复非密钥字段，凭据不回滚",
                }
            ),
            secret_fields=("secret",),
            test=test_jira,
            rebinds=jira_rebinds,
        ),
        AdminFunction(
            "start_jira_sync",
            "data_ai",
            "change",
            "normal",
            "开始同步 Jira，不能撤销",
            NoArgs,
            lambda d, p, a, **kw: sync.create_run(d, p, "jira", **kw),
            "/admin/data-ai?tab=sources",
            (("POST", "/sources/jira/sync"),),
        ),
        AdminFunction(
            "retry_sync_run",
            "data_ai",
            "change",
            "normal",
            "重试失败或停滞的同步，不能撤销",
            RetryArgs,
            lambda d, p, a, **kw: sync.retry_run(d, p, a.run_id, **kw),
            "/admin",
            (("POST", "/sync-runs/{run_id}/retry"),),
        ),
        AdminFunction(
            "link_identity",
            "data_ai",
            "change",
            "normal",
            "将一个 Jira 或工作记录账号关联到员工",
            LinkArgs,
            link_identity,
            "/admin/people?tab=identities",
            (("POST", "/external-identities/link"),),
            snapshot=identity_snapshot,
            reverse=restore_identity,
        ),
        AdminFunction(
            "unlink_identity",
            "data_ai",
            "change",
            "normal",
            "解除账号关联并撤销该账号独有的证据",
            IdentityArgs,
            lambda d, p, a, **kw: identities.unlink_identity(d, p, a.identity_id, **kw),
            "/admin/people?tab=identities",
            (("POST", "/external-identities/{identity_id}/unlink"),),
            snapshot=identity_snapshot,
            reverse=restore_identity,
        ),
    ]:
        functions[entry.name] = entry
