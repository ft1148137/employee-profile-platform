from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import case, func, or_
from sqlmodel import Session, col, select

from app import crud
from app.authz.principal import Principal
from app.core.config import settings
from app.core.security import get_password_hash
from app.domains.account.passwords import generate_password
from app.domains.account.schemas import (
    AccountPublic,
    AccountsPublic,
    AccountStatus,
    EmployeeAccount,
    GrantRole,
    InitialPassword,
    IssueAccounts,
    RolePublic,
)
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.org.schemas import DepartmentSummary
from app.models import User


class AccountError(HTTPException):
    pass


def require_admin(principal: Principal) -> None:
    if not principal.is_org_admin:
        raise AccountError(403, "需要企业管理员权限")


def lock_org(session: Session, principal: Principal) -> Organization:
    require_admin(principal)
    # Serialize account/role writes so concurrent removals cannot remove every admin.
    org = session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    if not session.exec(
        select(RoleGrant.id)
        .join(OrgMembership, col(OrgMembership.id) == RoleGrant.membership_id)
        .where(
            RoleGrant.org_id == principal.org_id,
            OrgMembership.org_id == principal.org_id,
            OrgMembership.user_id == principal.user_id,
            RoleGrant.role == Role.ORG_ADMIN,
        )
    ).first():
        raise AccountError(403, "需要企业管理员权限")
    return org


def audit(session: Session, p: Principal, action: str, target_id: UUID) -> None:
    record_audit(
        session,
        org_id=p.org_id,
        actor_type=p.actor_type,
        actor_id=str(p.user_id),
        action=action,
        target_type="role_grant" if action.startswith("role.") else "user",
        target_id=str(target_id),
        details={"id": str(target_id)},
    )


def member(session: Session, org_id: UUID, user_id: UUID) -> tuple[OrgMembership, User]:
    row = session.exec(
        select(OrgMembership, User)
        .join(User)
        .where(
            OrgMembership.org_id == org_id,
            OrgMembership.user_id == user_id,
        )
        .with_for_update(of=User)
    ).first()
    if row is None:
        raise AccountError(404, "本企业成员不存在")
    return row[0], row[1]


def check_employed(session: Session, membership: OrgMembership) -> None:
    if (
        membership.employee_id
        and session.exec(
            select(Employee).where(
                Employee.org_id == membership.org_id,
                Employee.id == membership.employee_id,
                Employee.employment_status == EmploymentStatus.INACTIVE,
            )
        ).first()
    ):
        raise AccountError(409, "该员工已离职，请先在名单里恢复")


def require_org_only_account(session: Session, org_id: UUID, user: User) -> None:
    if (
        user.is_superuser
        or session.exec(
            select(OrgMembership.id).where(
                OrgMembership.user_id == user.id, OrgMembership.org_id != org_id
            )
        ).first()
    ):
        raise AccountError(
            409, "这个账号同时属于其他企业，只能由部署人员在服务器上处理"
        )


def protect_admin(session: Session, org_id: UUID, membership_id: UUID) -> None:
    admins = session.exec(
        select(OrgMembership.id)
        .join(User)
        .join(RoleGrant, col(RoleGrant.membership_id) == OrgMembership.id)
        .outerjoin(
            Employee,
            (col(Employee.id) == OrgMembership.employee_id)
            & (col(Employee.org_id) == OrgMembership.org_id),
        )
        .where(
            OrgMembership.org_id == org_id,
            RoleGrant.org_id == org_id,
            RoleGrant.role == Role.ORG_ADMIN,
            col(User.is_active).is_(True),
            or_(
                col(Employee.id).is_(None),
                col(Employee.employment_status) == EmploymentStatus.ACTIVE,
            ),
        )
    ).all()
    if membership_id in admins and len(admins) == 1:
        raise AccountError(409, "不能移除最后一名有效的企业管理员")


def reset_password(user: User) -> InitialPassword:
    password = generate_password()
    user.hashed_password = get_password_hash(password)
    user.must_change_password = True
    user.password_changed_at = utc_now()
    return InitialPassword(
        username=user.username, display_name=user.full_name, initial_password=password
    )


def issue_accounts(
    session: Session, p: Principal, body: IssueAccounts
) -> tuple[Organization, list[list[str]]]:
    org = lock_org(session, p)
    query = select(Employee).where(Employee.org_id == p.org_id)
    if body.employee_ids is None:
        query = query.where(
            Employee.employment_status == EmploymentStatus.ACTIVE,
            col(Employee.employee_code).is_not(None),
            func.length(func.trim(Employee.employee_code)) > 0,
            ~select(OrgMembership.id)
            .where(
                OrgMembership.org_id == p.org_id,
                OrgMembership.employee_id == Employee.id,
            )
            .exists(),
        )
    else:
        query = query.where(col(Employee.id).in_(body.employee_ids))
    employees = session.exec(query.order_by(col(Employee.id)).with_for_update()).all()
    if body.employee_ids is not None and {e.id for e in employees} != set(
        body.employee_ids
    ):
        raise AccountError(404, "本企业员工不存在")
    conflicts = []
    candidates: list[tuple[Employee, User | None]] = []
    usernames: set[str] = set()
    for employee in employees:
        linked = session.exec(
            select(User)
            .join(OrgMembership)
            .where(
                OrgMembership.org_id == p.org_id,
                OrgMembership.employee_id == employee.id,
            )
            .with_for_update(of=User)
        ).first()
        code = (employee.employee_code or "").strip()
        reason = None
        if not code or "@" in code or len(code) > 255:
            reason = "工号为空或不能作为登录名"
        elif employee.employment_status != EmploymentStatus.ACTIVE:
            reason = "员工已离职"
        elif linked:
            try:
                require_org_only_account(session, p.org_id, linked)
            except AccountError as exc:
                reason = exc.detail
            if reason is None and not (
                body.regenerate_pending
                and linked.is_active
                and linked.must_change_password
            ):
                reason = "账号已存在，且不允许重新生成"
        elif (
            crud.get_user_by_username(session=session, username=code)
            or code.lower() in usernames
        ):
            reason = "登录名已被占用"
        usernames.add(code.lower())
        if reason:
            conflicts.append(
                {
                    "employee_code": employee.employee_code,
                    "display_name": employee.display_name,
                    "reason": reason,
                }
            )
        candidates.append((employee, linked))
    if conflicts:
        raise AccountError(409, conflicts)
    if not candidates:
        raise AccountError(422, "没有可以生成账号的员工")
    departments = {
        d.id: d.name
        for d in session.exec(
            select(Department).where(Department.org_id == p.org_id)
        ).all()
    }
    rows = []
    for employee, user in candidates:
        note = ""
        if user is None:
            email = employee.work_email or None
            if email and crud.get_user_by_email(session=session, email=email):
                email, note = None, "邮箱已被其他账号使用，暂不能用邮箱登录"
            user = User(
                username=(employee.employee_code or "").strip(),
                full_name=employee.display_name,
                email=email,
                hashed_password="",
            )
            session.add(user)
            session.flush()
            session.add(
                OrgMembership(org_id=p.org_id, user_id=user.id, employee_id=employee.id)
            )
        password = reset_password(user)
        session.add(user)
        audit(session, p, "account.issued", user.id)
        rows.append(
            [
                user.username,
                employee.display_name,
                departments.get(employee.department_id, "")
                if employee.department_id
                else "",
                str(settings.FRONTEND_HOST),
                password.initial_password,
                note,
            ]
        )
    return org, rows


def change_account(
    session: Session,
    p: Principal,
    user_id: UUID,
    action: Literal["password_reset", "disabled", "enabled"],
) -> InitialPassword | None:
    lock_org(session, p)
    membership, user = member(session, p.org_id, user_id)
    require_org_only_account(session, p.org_id, user)
    if user_id == p.user_id and action != "enabled":
        raise AccountError(409, "不能重置自己的密码或停用自己，请到设置页修改密码")
    result = None
    if action == "password_reset":
        result = reset_password(user)
    elif action == "disabled":
        protect_admin(session, p.org_id, membership.id)
        user.is_active = False
    else:
        check_employed(session, membership)
        user.is_active = True
    session.add(user)
    audit(session, p, f"account.{action}", user.id)
    session.commit()
    return result


def list_roles(session: Session, p: Principal) -> list[RolePublic]:
    require_admin(p)
    rows = session.exec(
        select(RoleGrant, User, Department)
        .join(OrgMembership, col(OrgMembership.id) == RoleGrant.membership_id)
        .join(User, col(User.id) == OrgMembership.user_id)
        .outerjoin(
            Department,
            (col(Department.id) == RoleGrant.department_id)
            & (col(Department.org_id) == RoleGrant.org_id),
        )
        .where(RoleGrant.org_id == p.org_id, OrgMembership.org_id == p.org_id)
        .order_by(col(RoleGrant.id))
    ).all()
    return [
        RolePublic(
            id=g.id,
            user_id=u.id,
            username=u.username,
            display_name=u.full_name,
            role=g.role,
            department_id=g.department_id,
            department=DepartmentSummary.model_validate(d) if d else None,
        )
        for g, u, d in rows
    ]


def grant_role(session: Session, p: Principal, body: GrantRole) -> RolePublic:
    lock_org(session, p)
    membership, user = member(session, p.org_id, body.user_id)
    require_org_only_account(session, p.org_id, user)
    if not user.is_active:
        raise AccountError(409, "目标账号已停用")
    check_employed(session, membership)
    if (
        body.department_id
        and not session.exec(
            select(Department.id).where(
                Department.org_id == p.org_id, Department.id == body.department_id
            )
        ).first()
    ):
        raise AccountError(404, "本企业部门不存在")
    grant = session.exec(
        select(RoleGrant).where(
            RoleGrant.org_id == p.org_id,
            RoleGrant.membership_id == membership.id,
            RoleGrant.role == body.role,
            RoleGrant.department_id == body.department_id,
        )
    ).first()
    if grant is None:
        grant = RoleGrant(
            org_id=p.org_id,
            membership_id=membership.id,
            role=body.role,
            department_id=body.department_id,
            granted_by_user_id=p.user_id,
        )
        session.add(grant)
    audit(session, p, "role.granted", grant.id)
    result = next(g for g in list_roles(session, p) if g.id == grant.id)
    session.commit()
    return result


def revoke_role(session: Session, p: Principal, grant_id: UUID) -> None:
    lock_org(session, p)
    grant = session.exec(
        select(RoleGrant).where(RoleGrant.org_id == p.org_id, RoleGrant.id == grant_id)
    ).first()
    if grant is None:
        raise AccountError(404, "本企业角色授予不存在")
    if grant.role == Role.ORG_ADMIN:
        protect_admin(session, p.org_id, grant.membership_id)
    session.delete(grant)
    audit(session, p, "role.revoked", grant.id)
    session.commit()


def list_accounts(
    session: Session,
    p: Principal,
    department_id: UUID | None,
    status: AccountStatus | None,
    q: str | None,
    skip: int,
    limit: int,
    employment_status: EmploymentStatus | None = None,
) -> AccountsPublic:
    require_admin(p)
    account_status = case(
        (col(User.id).is_(None), "none"),
        (col(User.is_active).is_(False), "disabled"),
        (col(User.must_change_password).is_(True), "pending_first_login"),
        else_="active",
    )
    query = select(Employee, User, Department).outerjoin(
        OrgMembership,
        (col(OrgMembership.org_id) == Employee.org_id)
        & (col(OrgMembership.employee_id) == Employee.id),
    )
    query = (
        query.outerjoin(User, col(User.id) == OrgMembership.user_id)
        .outerjoin(
            Department,
            (col(Department.org_id) == Employee.org_id)
            & (col(Department.id) == Employee.department_id),
        )
        .where(Employee.org_id == p.org_id)
    )
    if employment_status:
        query = query.where(Employee.employment_status == employment_status)
    if department_id:
        query = query.where(Employee.department_id == department_id)
    if status:
        query = query.where(account_status == status)
    if q:
        query = query.where(
            or_(
                col(Employee.display_name).icontains(q, autoescape=True),
                col(Employee.employee_code).icontains(q, autoescape=True),
                col(User.username).icontains(q, autoescape=True),
            )
        )
    count = session.exec(select(func.count()).select_from(query.subquery())).one()
    rows = session.exec(
        query.order_by(col(Employee.id)).offset(skip).limit(limit)
    ).all()
    roles: dict[UUID, list[RolePublic]] = {}
    for grant in list_roles(session, p):
        roles.setdefault(grant.user_id, []).append(grant)
    return AccountsPublic(
        count=count,
        data=[
            EmployeeAccount(
                **e.model_dump(
                    include={"id", "employee_code", "display_name", "employment_status"}
                ),
                department=DepartmentSummary.model_validate(d) if d else None,
                account=AccountPublic(
                    user_id=u.id,
                    username=u.username,
                    email=u.email,
                    last_login_at=u.last_login_at,
                    status="disabled"
                    if not u.is_active
                    else "pending_first_login"
                    if u.must_change_password
                    else "active",
                )
                if u
                else None,
                roles=roles.get(u.id, []) if u else [],
            )
            for e, u, d in rows
        ],
    )
