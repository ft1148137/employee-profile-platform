from collections import Counter, defaultdict
from uuid import UUID

from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    OrgMembership,
    ReportingLine,
    Role,
    RoleGrant,
)
from app.domains.org.roster.schemas import (
    CHANGE_KINDS,
    DepartmentNode,
    EmployeeChange,
    FieldChange,
    ParsedRoster,
    RosterDiff,
    RosterIssue,
)
from app.models import User


def compute_roster_diff(
    session: Session, org_id: UUID, roster: ParsedRoster
) -> RosterDiff:
    """Read five bounded queries; never flush even if the caller has pending writes."""
    if roster.has_errors:
        raise ValueError("名单有解析错误，不能对比")
    with session.no_autoflush:
        employees = session.exec(
            select(Employee)
            .where(Employee.org_id == org_id)
            .order_by(Employee.employee_code)
        ).all()
        departments = session.exec(
            select(Department).where(Department.org_id == org_id)
        ).all()
        lines = session.exec(
            select(ReportingLine).where(
                ReportingLine.org_id == org_id, col(ReportingLine.valid_to).is_(None)
            )
        ).all()
        # User emails are deployment-wide unique; inspect only incoming emails or this tenant's accounts.
        accounts = session.exec(
            select(User, OrgMembership)
            .outerjoin(
                OrgMembership,
                (col(OrgMembership.user_id) == User.id)
                & (OrgMembership.org_id == org_id),
            )
            .where(
                or_(
                    col(OrgMembership.org_id) == org_id,
                    func.lower(User.email).in_(
                        [row.email.lower() for row in roster.rows if row.email]
                    ),
                )
            )
        ).all()
        admins = session.exec(
            select(User, OrgMembership)
            .join(OrgMembership, col(OrgMembership.user_id) == User.id)
            .join(
                RoleGrant,
                (col(RoleGrant.membership_id) == OrgMembership.id)
                & (RoleGrant.org_id == org_id),
            )
            .where(OrgMembership.org_id == org_id, RoleGrant.role == Role.ORG_ADMIN)
        ).all()
    blockers: list[str] = []
    warnings: list[RosterIssue] = []
    changes: list[EmployeeChange] = []
    by_id = {e.id: e for e in employees}
    by_code: dict[str, Employee] = {}
    duplicates: dict[str, list[str]] = defaultdict(list)
    for employee in employees:
        if employee.employee_code:
            code = employee.employee_code.casefold()
            by_code[code] = employee
            duplicates[code].append(employee.employee_code)
    for codes in duplicates.values():
        if len(codes) > 1:
            blockers.append(f"库里存在只差大小写的工号：{'、'.join(codes)}")
    unkeyed = [e for e in employees if not e.employee_code]
    if unkeyed:
        warnings.append(
            RosterIssue(
                severity="warning",
                row_number=None,
                column=None,
                message=f"{len(unkeyed)} 名员工没有工号，不参与对比：{'、'.join(e.display_name for e in unkeyed)}",
            )
        )
    department_by_id = {d.id: d for d in departments}
    paths: dict[UUID, tuple[str, ...]] = {}
    for department in departments:
        path: tuple[str, ...] = ()
        current = department
        seen: set[UUID] = set()
        while True:
            if current.id in seen:
                raise ValueError("现有部门层级存在循环，无法对比名单")
            seen.add(current.id)
            path = (current.name, *path)
            if current.parent_department_id is None:
                break
            current = department_by_id[current.parent_department_id]
        paths[department.id] = path
    existing = {path: department_by_id[id] for id, path in paths.items()}
    incoming = {row.employee_code.casefold(): row for row in roster.rows}
    current_managers = {
        line.employee_id: by_id[line.manager_employee_id] for line in lines
    }
    linked = {
        membership.employee_id: user
        for user, membership in accounts
        if membership is not None
    }
    owners = {user.email.casefold(): user.id for user, _ in accounts if user.email}
    columns = set(roster.column_mapping.values())
    used: set[tuple[str, ...]] = set()
    counts: Counter[tuple[str, ...]] = Counter()
    for employee in unkeyed:
        if (
            employee.employment_status == EmploymentStatus.ACTIVE
            and employee.department_id is not None
            and employee.department_id in paths
        ):
            counts[paths[employee.department_id]] += 1
    for row in roster.rows:
        used.update(
            row.department_path[:i] for i in range(1, len(row.department_path) + 1)
        )
        counts[row.department_path] += 1
        matched = by_code.get(row.employee_code.casefold())
        base = EmployeeChange(
            kind="added",
            employee_id=matched.id if matched else None,
            employee_code=row.employee_code,
            display_name=row.display_name,
            row_number=row.row_number,
        )
        if matched is None:
            changes.append(base)
            continue
        fields = (
            [
                FieldChange(
                    field="display_name",
                    before=matched.display_name,
                    after=row.display_name,
                )
            ]
            if matched.display_name != row.display_name
            else []
        )
        if "职位" in columns and matched.title != row.title:
            fields.append(
                FieldChange(field="title", before=matched.title, after=row.title)
            )
        if (
            "邮箱" in columns
            and (matched.work_email or "").casefold() != (row.email or "").casefold()
        ):
            fields.append(
                FieldChange(field="email", before=matched.work_email, after=row.email)
            )
        if fields:
            changes.append(
                base.model_copy(update={"kind": "updated", "fields": fields})
            )
        old_path = paths.get(matched.department_id) if matched.department_id else None
        if old_path != row.department_path:
            changes.append(
                base.model_copy(
                    update={
                        "kind": "transferred",
                        "fields": [
                            FieldChange(
                                field="department",
                                before="/".join(old_path) if old_path else None,
                                after="/".join(row.department_path),
                            )
                        ],
                    }
                )
            )
        manager = current_managers.get(matched.id)
        if (
            "上级工号" in columns
            and ((manager.employee_code or "").casefold() if manager else "")
            != (row.manager_code or "").casefold()
        ):
            new_manager = (
                incoming[row.manager_code.casefold()] if row.manager_code else None
            )
            changes.append(
                base.model_copy(
                    update={
                        "kind": "manager_changed",
                        "fields": [
                            FieldChange(
                                field="manager",
                                before=f"{manager.display_name}（{manager.employee_code}）"
                                if manager
                                else None,
                                after=f"{new_manager.display_name}（{new_manager.employee_code}）"
                                if new_manager
                                else None,
                            )
                        ],
                    }
                )
            )
        if matched.employment_status == EmploymentStatus.INACTIVE:
            changes.append(base.model_copy(update={"kind": "reactivated"}))
        user = linked.get(matched.id)
        if (
            "邮箱" in columns
            and user
            and row.email
            and (user.email or "").casefold() != row.email.casefold()
            and owners.get(row.email.casefold(), user.id) != user.id
        ):
            warnings.append(
                RosterIssue(
                    severity="warning",
                    row_number=row.row_number,
                    column="邮箱",
                    message=f"第 {row.row_number} 行「邮箱」：{row.email} 已被其他账号使用，账号的邮箱不会同步",
                )
            )
    for employee in employees:
        if (
            employee.employee_code
            and employee.employee_code.casefold() not in incoming
            and employee.employment_status == EmploymentStatus.ACTIVE
        ):
            changes.append(
                EmployeeChange(
                    kind="deactivated",
                    employee_id=employee.id,
                    employee_code=employee.employee_code,
                    display_name=employee.display_name,
                    row_number=None,
                )
            )
    summary: dict[str, int] = {
        kind: sum(c.kind == kind for c in changes) for kind in CHANGE_KINDS
    }
    if summary["reactivated"]:
        warnings.append(
            RosterIssue(
                severity="warning",
                row_number=None,
                column=None,
                message=f"恢复在职的 {summary['reactivated']} 名员工账号仍然停用，需要企业管理员在『员工与账号』页启用并重置密码",
            )
        )

    def remains_active(employee: Employee) -> bool:
        return (
            employee.employee_code.casefold() in incoming
            if employee.employee_code
            else employee.employment_status == EmploymentStatus.ACTIVE
        )

    available = any(
        user.is_active
        and (
            membership.employee_id is None
            or remains_active(by_id[membership.employee_id])
        )
        for user, membership in admins
    )
    if not available:
        affected = [
            by_id[m.employee_id].employee_code
            for u, m in admins
            if u.is_active
            and m.employee_id
            and not remains_active(by_id[m.employee_id])
        ]
        blockers.append(
            "导入后企业将没有可用的企业管理员"
            + (
                f"：管理员账号关联的员工 {'、'.join(code or '无工号' for code in affected)} 会被停用或仍未在职"
                if affected
                else "：没有启用的管理员账号及在职关联员工"
            )
        )
    created = sorted(used - existing.keys(), key=lambda path: (len(path), path))
    unused = sorted(existing.keys() - used)
    nodes = {
        path: DepartmentNode(
            name=path[-1],
            path=path,
            department_id=existing[path].id if path in existing else None,
            is_new=path not in existing,
            employee_count=counts[path],
        )
        for path in sorted(existing.keys() | used)
    }
    for path, node in nodes.items():
        if len(path) > 1:
            nodes[path[:-1]].children.append(node)
    summary.update(departments_created=len(created), departments_unused=len(unused))
    active_count = sum(
        e.employment_status == EmploymentStatus.ACTIVE and bool(e.employee_code)
        for e in employees
    )
    return RosterDiff(
        summary=summary,
        changes=changes,
        departments=[node for path, node in nodes.items() if len(path) == 1],
        created_departments=created,
        unused_departments=unused,
        warnings=warnings,
        blockers=blockers,
        active_count=active_count,
        requires_mass_deactivation_confirm=summary["deactivated"] > 5
        and summary["deactivated"] * 5 > active_count,
    )
