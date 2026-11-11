import csv
import hashlib
from io import StringIO
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.account.service import lock_org
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.org.employee_fields import FIELD_LABELS
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    ExternalIdentity,
    OrgMembership,
    ReportingLine,
    RosterImport,
)
from app.domains.org.roster.diff import compute_roster_diff
from app.domains.org.roster.parser import parse_roster, validate_roster
from app.domains.org.roster.schemas import (
    CHANGE_KINDS,
    HEADERS,
    ApplyImport,
    ParsedRoster,
    RosterDiff,
    RosterImportHistory,
    RosterImportPreview,
    RosterImportResult,
    RosterImportsPublic,
    RosterIssue,
)
from app.domains.source.linking import auto_link_identities
from app.domains.work.ingest import rederive_identities
from app.models import User, uses_email_login

MAX_FILE_SIZE = 5 * 1024 * 1024


class RosterError(HTTPException):
    pass


def create_import(
    session: Session,
    principal: Principal,
    content: bytes | ParsedRoster,
    filename: str,
    *,
    commit: bool = True,
) -> RosterImportPreview:
    org = lock_org(session, principal)
    assert principal.user_id is not None
    if isinstance(content, ParsedRoster):
        preserved = [row for row in content.rows if row.inactive]
        output = StringIO()
        writer = csv.writer(output)
        fields = [
            f for f, label in FIELD_LABELS.items() if label in content.column_mapping
        ]
        writer.writerow([*HEADERS, *(FIELD_LABELS[f] for f in fields)])
        writer.writerows(
            (
                r.employee_code,
                r.display_name,
                "/".join(r.department_path),
                r.title,
                r.manager_code,
                r.email,
                *(getattr(r, f) for f in fields),
            )
            for r in content.active().rows
        )
        content = output.getvalue().encode()
        roster = parse_roster(content, "人员变动.csv", org.employee_fields)
        roster.rows.extend(preserved)
    else:
        roster = parse_roster(content, filename, org.employee_fields)
    diff = (
        RosterDiff(
            summary=dict.fromkeys(
                (*CHANGE_KINDS, "departments_created", "departments_unused"), 0
            ),
            changes=[],
            departments=[],
            created_departments=[],
            unused_departments=[],
            warnings=[],
            blockers=[],
            active_count=0,
            requires_mass_deactivation_confirm=False,
        )
        if roster.has_errors
        else compute_roster_diff(session, principal.org_id, roster)
    )
    issues = [
        *roster.issues,
        *diff.warnings,
        *[
            RosterIssue(severity="error", row_number=None, column=None, message=message)
            for message in diff.blockers
        ],
    ]
    record = RosterImport(
        org_id=principal.org_id,
        filename=filename,
        file_sha256=hashlib.sha256(content).hexdigest(),
        rows=[row.model_dump(mode="json") for row in roster.rows],
        column_mapping=roster.column_mapping,
        issues=[issue.model_dump(mode="json") for issue in issues],
        summary=diff.summary,
        base_revision=org.roster_revision,
        uploaded_by_user_id=principal.user_id,
    )
    session.add(record)
    session.flush()
    if commit:
        session.commit()
    return RosterImportPreview(
        **diff.model_dump(),
        id=record.id,
        base_revision=record.base_revision,
        column_mapping=roster.column_mapping,
        issues=issues,
        can_apply=not any(issue.severity == "error" for issue in issues),
    )


def apply_import(
    session: Session,
    principal: Principal,
    import_id: UUID,
    body: ApplyImport,
    *,
    commit: bool = True,
) -> RosterImportResult:
    org = lock_org(session, principal)
    record = session.exec(
        select(RosterImport).where(
            RosterImport.org_id == principal.org_id, RosterImport.id == import_id
        )
    ).first()
    if record is None:
        raise RosterError(404, "没有这份名单")
    if record.status == "applied":
        raise RosterError(409, "这份名单已经导入过")
    stale_message = "名单在预览之后被更新过，请重新上传"
    if record.status == "stale":
        raise RosterError(409, stale_message)
    if (
        body.base_revision != org.roster_revision
        or record.base_revision != org.roster_revision
    ):
        record.status = "stale"
        session.add(record)
        session.flush()
        if commit:
            session.commit()
        raise RosterError(409, stale_message)
    roster = ParsedRoster.model_validate(
        {"rows": record.rows, "column_mapping": record.column_mapping}
    ).active()
    validate_roster(roster)
    if roster.has_errors:
        raise RosterError(
            422, [issue.message for issue in roster.issues if issue.severity == "error"]
        )
    errors = [
        issue["message"] for issue in record.issues if issue["severity"] == "error"
    ]
    if errors:
        raise RosterError(422, errors)
    diff = compute_roster_diff(session, principal.org_id, roster)
    if diff.blockers:
        raise RosterError(422, diff.blockers)
    if diff.requires_mass_deactivation_confirm and not body.confirm_mass_deactivation:
        raise RosterError(
            422,
            {
                "code": "mass_deactivation_confirm_required",
                "message": f"本次导入会停用 {diff.summary['deactivated']} 名员工，请确认后再导入",
            },
        )
    paths: dict[tuple[str, ...], UUID] = {}
    nodes = list(diff.departments)
    while nodes:
        node = nodes.pop()
        if node.department_id:
            paths[node.path] = node.department_id
        nodes.extend(node.children)
    for path in diff.created_departments:
        department = Department(
            org_id=principal.org_id,
            name=path[-1],
            parent_department_id=paths[path[:-1]] if len(path) > 1 else None,
        )
        session.add(department)
        session.flush()
        paths[path] = department.id
    employees = session.exec(
        select(Employee).where(Employee.org_id == principal.org_id)
    ).all()
    by_code = {e.employee_code.casefold(): e for e in employees if e.employee_code}
    accounts = dict(
        session.exec(
            select(OrgMembership.employee_id, User)
            .join(User)
            .where(
                OrgMembership.org_id == principal.org_id,
                col(OrgMembership.employee_id).is_not(None),
            )
            .with_for_update(of=User)
        ).all()
    )
    columns = set(roster.column_mapping.values())
    now = utc_now()
    for row in roster.rows:
        employee = by_code.get(row.employee_code.casefold())
        if employee is None:
            employee = Employee(
                org_id=principal.org_id,
                employee_code=row.employee_code,
                display_name=row.display_name,
            )
            by_code[row.employee_code.casefold()] = employee
        employee.display_name = row.display_name
        employee.department_id = paths[row.department_path]
        employee.employment_status = EmploymentStatus.ACTIVE
        employee.updated_at = now
        if "职位" in columns:
            employee.title = row.title
        if "邮箱" in columns:
            employee.work_email = row.email
        for field, label in FIELD_LABELS.items():
            if label in columns:
                setattr(employee, field, getattr(row, field))
        session.add(employee)
        account = accounts.get(employee.id)
        if account:
            account.full_name = row.display_name
            if row.email and (
                not uses_email_login(account) or account.username != row.email
            ):
                account.username = row.email.strip().lower()
                account.email = account.username
                record_audit(
                    session,
                    org_id=principal.org_id,
                    actor_type=principal.actor_type,
                    actor_id=str(principal.user_id),
                    action="account.email_changed",
                    target_type="user",
                    target_id=str(account.id),
                    details={"id": str(account.id)},
                    flush=False,
                )
            session.add(account)
    deactivated: set[UUID] = set()
    for change in diff.changes:
        if change.kind != "deactivated":
            continue
        employee = by_code[change.employee_code.casefold()]
        employee.employment_status = EmploymentStatus.INACTIVE
        employee.updated_at = now
        session.add(employee)
        deactivated.add(employee.id)
        account = accounts.get(employee.id)
        if account:
            account.is_active = False
            session.add(account)
    session.flush()
    lines = {
        line.employee_id: line
        for line in session.exec(
            select(ReportingLine).where(
                ReportingLine.org_id == principal.org_id,
                col(ReportingLine.valid_to).is_(None),
            )
        ).all()
    }
    managers = (
        {
            by_code[row.employee_code.casefold()].id: by_code[
                row.manager_code.casefold()
            ].id
            if row.manager_code
            else None
            for row in roster.rows
        }
        if "上级工号" in columns
        else {}
    )
    for employee_id, line in lines.items():
        if employee_id in deactivated or (
            employee_id in managers
            and managers[employee_id] != line.manager_employee_id
        ):
            line.valid_to = now
            session.add(line)
    # Close old lines before inserting replacements under the partial unique index.
    session.flush()
    for employee_id, manager_id in managers.items():
        old = lines.get(employee_id)
        if manager_id and (old is None or old.manager_employee_id != manager_id):
            session.add(
                ReportingLine(
                    org_id=principal.org_id,
                    employee_id=employee_id,
                    manager_employee_id=manager_id,
                    valid_from=now,
                    roster_import_id=record.id,
                )
            )
    identities = session.exec(
        select(ExternalIdentity)
        .where(
            ExternalIdentity.org_id == principal.org_id,
            col(ExternalIdentity.employee_id).is_(None),
            ~col(ExternalIdentity.auto_link_blocked),
        )
        .order_by(col(ExternalIdentity.id))
        .with_for_update()
    ).all()
    linked = auto_link_identities(session, principal.org_id, identities)
    rederive_identities(session, org_id=principal.org_id, identities=linked)
    org.roster_revision += 1
    record.status = "applied"
    record.applied_at = now
    record.applied_by_user_id = principal.user_id
    record.summary = diff.summary
    issues = diff.warnings
    record.issues = [issue.model_dump(mode="json") for issue in issues]
    session.add_all([org, record])
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="roster.imported",
        target_type="roster_import",
        target_id=str(record.id),
        details={"id": str(record.id), **diff.summary},
    )
    session.flush()
    if commit:
        session.commit()
    return RosterImportResult(
        id=record.id, status="applied", summary=record.summary, issues=issues
    )


def list_imports(
    session: Session, principal: Principal, skip: int, limit: int
) -> RosterImportsPublic:
    query = (
        select(RosterImport, User)
        .join(User, col(User.id) == RosterImport.uploaded_by_user_id)
        .where(RosterImport.org_id == principal.org_id)
    )
    rows = session.exec(
        query.order_by(col(RosterImport.created_at).desc(), col(RosterImport.id).desc())
        .offset(skip)
        .limit(limit)
    ).all()
    count = session.exec(
        select(func.count())
        .select_from(RosterImport)
        .where(RosterImport.org_id == principal.org_id)
    ).one()
    return RosterImportsPublic(
        data=[
            RosterImportHistory(
                **record.model_dump(),
                uploaded_by_username=user.username,
                uploaded_by_full_name=user.full_name,
            )
            for record, user in rows
        ],
        count=count,
    )
