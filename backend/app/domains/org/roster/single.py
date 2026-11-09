"""Personnel changes use the same complete-roster validation and application."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, RootModel
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.account.service import lock_org, require_admin
from app.domains.change_request.models import ProfileChangeRequest, RequestStatus
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    Organization,
    ReportingLine,
)
from app.domains.org.paths import department_paths
from app.domains.org.roster import service
from app.domains.org.roster.schemas import (
    HEADERS,
    ApplyImport,
    ParsedRoster,
    RosterImportPreview,
    RosterImportResult,
    RosterRow,
)
from app.domains.org.service import EmployeeNotFound, visible_employee


class PersonnelInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Onboard(PersonnelInput):
    kind: Literal["onboard"] = "onboard"
    employee_code: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=1, max_length=320)
    department: str = Field(min_length=1, max_length=1000)
    title: str | None = None
    manager_id: UUID | None = None


class Transfer(PersonnelInput):
    kind: Literal["transfer"] = "transfer"
    employee_id: UUID
    department: str | None = Field(default=None, min_length=1, max_length=1000)
    title: str | None = None
    manager_id: UUID | None = None


class Offboard(PersonnelInput):
    kind: Literal["offboard"] = "offboard"
    employee_ids: list[UUID] = Field(min_length=1, max_length=5000)


SingleChange = Annotated[Onboard | Transfer | Offboard, Field(discriminator="kind")]


def current_roster(db: Session, p: Principal) -> ParsedRoster:
    require_admin(p)
    employees = db.exec(
        select(Employee)
        .where(Employee.org_id == p.org_id)
        .order_by(Employee.employee_code)
    ).all()
    by_id = {e.id: e for e in employees}
    paths = department_paths(
        db.exec(select(Department).where(Department.org_id == p.org_id)).all()
    )
    managers = {
        line.employee_id: by_id[line.manager_employee_id].employee_code
        for line in db.exec(
            select(ReportingLine).where(
                ReportingLine.org_id == p.org_id, col(ReportingLine.valid_to).is_(None)
            )
        ).all()
    }
    return ParsedRoster(
        column_mapping={h: h for h in HEADERS},
        # shortcut: unkeyed employees follow import exclusions; revisit if roster keys change.
        rows=[
            RosterRow(
                row_number=i + 2,
                employee_code=e.employee_code,
                display_name=e.display_name,
                department_path=tuple(paths.get(e.department_id, "").split("/"))
                if e.department_id
                else (),
                title=e.title,
                email=e.work_email,
                manager_code=managers.get(e.id),
                inactive=e.employment_status == EmploymentStatus.INACTIVE,
            )
            for i, e in enumerate(employees)
            if e.employee_code
        ],
    )


def targets(
    db: Session,
    p: Principal,
    args: Onboard | Transfer | Offboard,
    *,
    active: bool = True,
) -> list[Employee]:
    require_admin(p)
    ids = (
        args.employee_ids
        if isinstance(args, Offboard)
        else [args.employee_id]
        if isinstance(args, Transfer)
        else []
    )
    unique_ids = list(dict.fromkeys(ids))
    found = {
        e.id: e
        for e in db.exec(
            select(Employee).where(
                employee_scope_clause(p), col(Employee.id).in_(unique_ids)
            )
        ).all()
    }
    if len(found) != len(unique_ids):
        raise EmployeeNotFound()
    result = [found[id] for id in unique_ids]
    for employee in result:
        if active and (
            not employee.employee_code
            or employee.employment_status != EmploymentStatus.ACTIVE
        ):
            raise service.RosterError(409, "员工须在职且有工号，请先核对名单")
    return result


def prepare(
    db: Session, p: Principal, args: Onboard | Transfer | Offboard
) -> ParsedRoster:
    employees = targets(db, p, args)
    roster = current_roster(db, p)
    if isinstance(args, Offboard):
        codes = {e.employee_code for e in employees}
        roster.rows = [r for r in roster.rows if r.employee_code not in codes]
        for row in roster.rows:
            if not row.inactive and row.manager_code in codes:
                row.manager_code = None
    else:
        manager_code = None
        if args.manager_id:
            manager = visible_employee(db, p, args.manager_id)
            if (
                manager.employment_status != EmploymentStatus.ACTIVE
                or not manager.employee_code
            ):
                raise service.RosterError(409, "上级须在职且有工号")
            manager_code = manager.employee_code
        if isinstance(args, Onboard):
            if any(
                r.employee_code.casefold() == args.employee_code.casefold()
                for r in roster.rows
            ):
                raise service.RosterError(409, "工号已存在，请核对名单")
            row = RosterRow(
                row_number=len(roster.rows) + 2,
                employee_code=args.employee_code,
                display_name=args.display_name,
                email=args.email,
                department_path=(),
                title=args.title,
                manager_code=manager_code,
            )
            roster.rows.append(row)
        else:
            row = next(
                r for r in roster.rows if r.employee_code == employees[0].employee_code
            )
            if "title" in args.model_fields_set:
                row.title = args.title
            if "manager_id" in args.model_fields_set:
                row.manager_code = manager_code
        if args.department is not None:
            row.department_path = tuple(
                part.strip()
                for part in args.department.replace("／", "/").split("/")
                if part.strip()
            )
        if not row.department_path:
            raise service.RosterError(422, "部门不能为空")
    return roster


def preview(
    db: Session, p: Principal, args: SingleChange, *, commit: bool = True
) -> RosterImportPreview:
    lock_org(db, p)
    return service.create_import(db, p, prepare(db, p, args), "人员变动", commit=commit)


def apply(
    db: Session, p: Principal, args: SingleChange, *, commit: bool = True
) -> RosterImportResult:
    record = preview(db, p, args, commit=False)
    return service.apply_import(
        db,
        p,
        record.id,
        ApplyImport(
            base_revision=record.base_revision,
            confirm_mass_deactivation=True,
        ),
        commit=commit,
    )


def snapshot(db: Session, p: Principal, args: SingleChange) -> BaseModel:
    selected = targets(db, p, args, active=False)
    roster = current_roster(db, p)
    codes = {e.employee_code for e in selected}
    return RootModel(
        {
            "roster_revision": db.exec(
                select(Organization.roster_revision).where(Organization.id == p.org_id)
            ).one(),
            "employees": [
                r.model_dump(mode="json")
                for r in roster.rows
                if r.employee_code in codes
            ],
        }
    )


def impact(db: Session, p: Principal, args: SingleChange) -> BaseModel:
    employees = targets(db, p, args)
    roster = prepare(db, p, args)
    if isinstance(args, Offboard):
        pending = db.exec(
            select(func.count())
            .select_from(ProfileChangeRequest)
            .where(
                ProfileChangeRequest.org_id == p.org_id,
                col(ProfileChangeRequest.subject_employee_id).in_(
                    [e.id for e in employees]
                ),
                col(ProfileChangeRequest.status).in_(
                    [
                        RequestStatus.PENDING_OWNER,
                        RequestStatus.PENDING_REVIEW,
                        RequestStatus.CHANGES_REQUESTED,
                    ]
                ),
            )
        ).one()
        text = f"离职：{'、'.join(e.display_name for e in employees)}；名下有 {pending} 个待处理的画像请求；账号将停用，直属下属将清空上级，画像和证据保留"
    else:
        code = (
            args.employee_code
            if isinstance(args, Onboard)
            else employees[0].employee_code
        )
        row = next(r for r in roster.rows if r.employee_code == code)
        manager = next(
            (
                r.display_name
                for r in roster.rows
                if r.employee_code == row.manager_code
            ),
            "无",
        )
        text = f"{row.display_name}；部门：{'/'.join(row.department_path)}；职位：{row.title or '未填写'}；上级：{manager}"
    return RootModel(
        {"count": len(employees) if isinstance(args, Offboard) else 1, "text": text}
    )
