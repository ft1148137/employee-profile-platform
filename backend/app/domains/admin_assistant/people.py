"""Account and dispute changes use the same services as the manual console."""

from base64 import b64encode
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, RootModel
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.account import service as accounts
from app.domains.account.export import export_accounts
from app.domains.account.schemas import GrantRole, IssueAccounts, RolePublic
from app.domains.admin_assistant.outputs import AccountsOutput, PasswordOutput
from app.domains.org.models import Employee, EmploymentStatus, OrgMembership, Role
from app.domains.review import appeal
from app.domains.review.appeal_schemas import ResolveAppealInput
from app.models import User

if TYPE_CHECKING:
    from app.domains.admin_assistant.registry import (
        AdminFunction,
        Endpoint,
        Handler,
        Risk,
        Snapshot,
    )


class AccountArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID


class DisableArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_ids: list[UUID] = Field(min_length=1, max_length=5000)


class IssueArgs(IssueAccounts):
    model_config = ConfigDict(extra="forbid")


class RoleArgs(GrantRole):
    model_config = ConfigDict(extra="forbid")


class RevokeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grant_id: UUID


class ResolveArgs(ResolveAppealInput):
    appeal_id: UUID


def _accounts(db: Session, p: Principal, ids: set[UUID]) -> BaseModel:
    rows = db.exec(
        select(User, Employee)
        .join(OrgMembership, col(OrgMembership.user_id) == User.id)
        .outerjoin(
            Employee,
            (col(Employee.org_id) == OrgMembership.org_id)
            & (col(Employee.id) == OrgMembership.employee_id),
        )
        .where(OrgMembership.org_id == p.org_id, col(User.id).in_(ids))
        .order_by(col(User.id))
        .with_for_update(of=User)
    ).all()
    if {u.id for u, _ in rows} != ids:
        raise accounts.AccountError(404, "本企业成员不存在")
    roles = accounts.list_roles(db, p)
    return RootModel(
        {
            "accounts": [
                {
                    **u.model_dump(
                        mode="json",
                        include={
                            "id",
                            "username",
                            "full_name",
                            "is_active",
                            "must_change_password",
                            "password_changed_at",
                        },
                    ),
                    "employee": e.model_dump(mode="json") if e else None,
                    "roles": [
                        r.model_dump(mode="json") for r in roles if r.user_id == u.id
                    ],
                }
                for u, e in rows
            ]
        }
    )


def account_snapshot(db: Session, p: Principal, args: Any) -> BaseModel:
    return _accounts(
        db, p, set(args.user_ids if isinstance(args, DisableArgs) else [args.user_id])
    )


def issue_snapshot(db: Session, p: Principal, args: IssueArgs) -> BaseModel:
    query = select(Employee).where(Employee.org_id == p.org_id)
    if args.employee_ids is not None:
        query = query.where(col(Employee.id).in_(args.employee_ids))
    else:
        query = query.where(Employee.employment_status == EmploymentStatus.ACTIVE)
    employees = db.exec(query.order_by(col(Employee.id))).all()
    if args.employee_ids is not None and {e.id for e in employees} != set(
        args.employee_ids
    ):
        raise accounts.AccountError(404, "本企业员工不存在")
    linked = dict(
        db.exec(
            select(OrgMembership.employee_id, OrgMembership.user_id).where(
                OrgMembership.org_id == p.org_id,
                col(OrgMembership.employee_id).in_([e.id for e in employees]),
            )
        ).all()
    )
    states = {
        u["id"]: u
        for u in _accounts(db, p, set(linked.values())).model_dump(mode="json")[
            "accounts"
        ]
    }
    result = [
        {
            "employee": e.model_dump(mode="json"),
            "account": states.get(str(linked.get(e.id))),
        }
        for e in employees
        if args.employee_ids is not None
        or (e.id not in linked and (e.employee_code or "").strip())
    ]
    return RootModel({"employees": result})


def revoke_snapshot(db: Session, p: Principal, args: RevokeArgs) -> RolePublic:
    role = next((r for r in accounts.list_roles(db, p) if r.id == args.grant_id), None)
    if role is None:
        raise accounts.AccountError(404, "本企业角色授予不存在")
    return role


def people_impact(db: Session, p: Principal, args: Any) -> BaseModel:
    if isinstance(args, IssueArgs):
        rows = issue_snapshot(db, p, args).model_dump()["employees"]
        names = [r["employee"]["display_name"] for r in rows]
        text = "发放账号：" + "、".join(names)
    elif isinstance(args, RevokeArgs):
        role = revoke_snapshot(db, p, args)
        names = [role.display_name or role.username]
        text = f"撤销{names[0]}的" + (
            "企业管理员"
            if role.role == Role.ORG_ADMIN
            else f"{role.department.name if role.department else ''}部门经理"
        )
    elif isinstance(args, ResolveArgs):
        row = appeal.read_appeal(p, db, args.appeal_id)
        employee = db.exec(
            select(Employee).where(
                Employee.org_id == p.org_id, Employee.id == row.employee_id
            )
        ).one()
        names = [employee.display_name]
        text = f"处理{employee.display_name}的争议：" + (
            "允许重新提交" if args.status == "reopened" else "维持原决定"
        )
    else:
        rows = account_snapshot(db, p, args).model_dump()["accounts"]
        names = [r["full_name"] or r["username"] for r in rows]
        text = "涉及账号：" + "、".join(names)
        if isinstance(args, RoleArgs):
            from app.domains.org.models import Department

            department = (
                db.exec(
                    select(Department).where(
                        Department.org_id == p.org_id,
                        Department.id == args.department_id,
                    )
                ).first()
                if args.department_id
                else None
            )
            if args.department_id and department is None:
                raise accounts.AccountError(404, "本企业部门不存在")
            text += "；授予" + (
                "企业管理员"
                if args.role == Role.ORG_ADMIN
                else f"{department.name if department else ''}部门经理"
            )
    return RootModel({"count": len(names), "text": text})


def issue(
    db: Session, p: Principal, args: IssueArgs, *, commit: bool = False
) -> AccountsOutput:
    org, rows = accounts.issue_accounts(db, p, args)
    result = AccountsOutput(
        filename=f"账号-{org.name}.xlsx",
        content_base64=b64encode(export_accounts(rows)).decode(),
        count=len(rows),
    )
    if commit:
        db.commit()
    return result


def reset(
    db: Session, p: Principal, args: AccountArgs, *, commit: bool = False
) -> PasswordOutput:
    result = accounts.change_account(
        db, p, args.user_id, "password_reset", commit=commit
    )
    assert result is not None
    return PasswordOutput(**result.model_dump())


def disable(
    db: Session, p: Principal, args: DisableArgs, *, commit: bool = False
) -> BaseModel:
    for uid in dict.fromkeys(args.user_ids):
        accounts.change_account(db, p, uid, "disabled", commit=False)
    if commit:
        db.commit()
    return RootModel({"count": len(set(args.user_ids))})


def revoke(
    db: Session, p: Principal, args: RevokeArgs, *, commit: bool = False
) -> BaseModel:
    accounts.revoke_role(db, p, args.grant_id, commit=commit)
    return RootModel({})


def register_people(functions: dict[str, AdminFunction]) -> None:
    from app.domains.admin_assistant.registry import AdminFunction

    definitions: list[
        tuple[
            str,
            str,
            type[BaseModel],
            Handler,
            Snapshot,
            Risk | Callable[[Any], Risk],
            Endpoint,
        ]
    ] = [
        (
            "issue_accounts",
            "发放账号",
            IssueArgs,
            issue,
            issue_snapshot,
            "normal",
            ("POST", "/accounts/issue"),
        ),
        (
            "reset_password",
            "重置密码",
            AccountArgs,
            reset,
            account_snapshot,
            "high",
            ("POST", "/accounts/{user_id}/reset-password"),
        ),
        (
            "disable_accounts",
            "停用账号",
            DisableArgs,
            disable,
            account_snapshot,
            lambda a: "high" if len(set(a.user_ids)) >= 2 else "normal",
            ("POST", "/accounts/{user_id}/disable"),
        ),
        (
            "enable_account",
            "启用账号",
            AccountArgs,
            lambda d, p, a, **kw: (
                accounts.change_account(d, p, a.user_id, "enabled", **kw)
                or RootModel({})
            ),
            account_snapshot,
            "normal",
            ("POST", "/accounts/{user_id}/enable"),
        ),
        (
            "grant_role",
            "授予角色",
            RoleArgs,
            accounts.grant_role,
            account_snapshot,
            lambda a: "high" if a.role == Role.ORG_ADMIN else "normal",
            ("POST", "/role-grants"),
        ),
        (
            "revoke_role",
            "撤销角色",
            RevokeArgs,
            revoke,
            revoke_snapshot,
            "normal",
            ("DELETE", "/role-grants/{grant_id}"),
        ),
        (
            "resolve_appeal",
            "处理争议",
            ResolveArgs,
            lambda d, p, a, **kw: appeal.resolve_appeal(p, d, a.appeal_id, a),
            lambda d, p, a: appeal.read_appeal(p, d, a.appeal_id),
            "normal",
            ("POST", "/appeals/{appeal_id}/resolve"),
        ),
    ]
    for name, title, model, handler, snapshot, risk, endpoint in definitions:
        functions[name] = AdminFunction(
            name,
            "people",
            "change",
            risk,
            title,
            model,
            handler,
            "/admin/people?tab=accounts",
            (endpoint,),
            snapshot=snapshot,
            impact=people_impact,
            undoable=False,
        )
