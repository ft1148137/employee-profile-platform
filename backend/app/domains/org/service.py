from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import case, func, or_
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Subquery
from sqlmodel import Session, col, select
from sqlmodel.sql.expression import Select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.change_request.models import (
    ChangeItem,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
    ReviewDecision,
    ReviewVia,
)
from app.domains.change_request.service import review_statuses
from app.domains.evidence.models import Evidence, EvidenceKind, EvidenceStatus
from app.domains.org.models import (
    Department,
    Employee,
    EmployeeField,
    EmploymentStatus,
    Organization,
    OrgMembership,
    ReportingLine,
    Role,
    RoleGrant,
)
from app.domains.org.schemas import (
    DepartmentManager,
    DepartmentSummary,
    EmployeeReference,
    EmployeesPublic,
    EmployeeSummary,
    InitialProfileStatus,
    OrganizationPublic,
    OrgChartDepartment,
    OrgChartPerson,
    OrgChartPublic,
    ProgressPublic,
    PublishedSummary,
)
from app.domains.profile.models import (
    InferenceStatus,
    ProfileProgress,
    ProfileVersion,
    ProgressReviewStatus,
)
from app.domains.source.models import SourceConnection
from app.models import User


class OrgChartForbidden(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "需要企业管理员或部门经理权限")


def read_org_chart(session: Session, principal: Principal) -> OrgChartPublic:
    if not principal.is_org_admin and not principal.managed_department_ids:
        raise OrgChartForbidden()
    departments = session.exec(
        select(Department)
        .where(
            Department.org_id == principal.org_id,
            True
            if principal.is_org_admin
            else col(Department.id).in_(principal.managed_department_ids),
        )
        .order_by(Department.name, col(Department.id))
    ).all()
    nodes = {d.id: OrgChartDepartment.model_validate(d) for d in departments}
    for node in nodes.values():
        if node.parent_department_id not in nodes:
            node.parent_department_id = None
        ancestor = node.parent_department_id
        seen = {node.id}
        while ancestor is not None and ancestor in nodes:
            if ancestor in seen:
                node.parent_department_id = None
                break
            seen.add(ancestor)
            ancestor = nodes[ancestor].parent_department_id
    managers_query = _reporting_managers(principal)
    # shortcut: return all people at once; paginate by department above 2,000 people.
    rows = session.exec(
        Select[tuple[Employee, UUID | None]](Employee, managers_query.c.id)
        .outerjoin(managers_query, col(Employee.id) == managers_query.c.employee_id)
        .where(
            employee_scope_clause(principal),
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
        .order_by(Employee.display_name, col(Employee.id))
    ).all()
    visible_ids = {employee.id for employee, _ in rows}
    people = [
        OrgChartPerson(
            **employee.model_dump(),
            manager_id=manager_id if manager_id in visible_ids else None,
        )
        for employee, manager_id in rows
    ]
    for person in people:
        if person.department_id is not None and person.department_id in nodes:
            nodes[person.department_id].direct_count += 1
    for node in nodes.values():
        ancestor = node.id
        seen = set()
        while ancestor is not None and ancestor in nodes and ancestor not in seen:
            seen.add(ancestor)
            nodes[ancestor].total_count += node.direct_count
            ancestor = nodes[ancestor].parent_department_id
    managers = session.exec(
        select(RoleGrant.department_id, Employee, User)
        .join(
            OrgMembership,
            (col(OrgMembership.id) == RoleGrant.membership_id)
            & (OrgMembership.org_id == principal.org_id),
        )
        .join(User, col(User.id) == OrgMembership.user_id)
        .outerjoin(
            Employee,
            (col(Employee.id) == OrgMembership.employee_id)
            & (Employee.org_id == principal.org_id),
        )
        .where(
            RoleGrant.org_id == principal.org_id,
            RoleGrant.role == Role.DEPT_MANAGER,
            col(RoleGrant.department_id).in_(nodes),
            or_(col(Employee.id).is_(None), employee_scope_clause(principal)),
        )
        .order_by(User.username, col(User.id))
    ).all()
    for department_id, employee, user in managers:
        if department_id in nodes:
            nodes[department_id].managers.append(
                DepartmentManager(
                    employee_id=employee.id if employee else None,
                    display_name=employee.display_name if employee else user.username,
                )
            )
    return OrgChartPublic(departments=list(nodes.values()), people=people)


def _reporting_managers(principal: Principal) -> Subquery:
    return (
        select(
            ReportingLine.employee_id,
            col(Employee.id).label("id"),
            Employee.display_name,
        )
        .join(
            Employee,
            (col(Employee.id) == ReportingLine.manager_employee_id)
            & (Employee.org_id == ReportingLine.org_id),
        )
        .where(
            ReportingLine.org_id == principal.org_id,
            col(ReportingLine.valid_to).is_(None),
            col(ReportingLine.employee_id).in_(
                select(Employee.id)
                .where(employee_scope_clause(principal))
                .correlate(None)
            ),
        )
        .subquery()
    )


def _initial_profile_status() -> ColumnElement[str]:
    initial = (
        select(ProfileChangeRequest.id)
        .where(
            ProfileChangeRequest.org_id == Employee.org_id,
            ProfileChangeRequest.subject_employee_id == Employee.id,
            ProfileChangeRequest.origin == RequestOrigin.INITIAL_SYNC,
        )
        .correlate(Employee)
    )
    has_items = (
        select(ChangeItem.id)
        .where(
            ChangeItem.org_id == ProfileChangeRequest.org_id,
            ChangeItem.request_id == ProfileChangeRequest.id,
        )
        .exists()
    )
    awaiting = [RequestStatus.DRAFT, RequestStatus.PENDING_OWNER]
    pending = initial.where(
        col(ProfileChangeRequest.status).in_(awaiting), has_items
    ).exists()
    handled = initial.where(~col(ProfileChangeRequest.status).in_(awaiting)).exists()
    direct_edited = (
        select(ProfileChangeRequest.id)
        .join(
            ReviewDecision,
            (col(ReviewDecision.org_id) == ProfileChangeRequest.org_id)
            & (col(ReviewDecision.request_id) == ProfileChangeRequest.id),
        )
        .where(
            ProfileChangeRequest.org_id == Employee.org_id,
            ProfileChangeRequest.subject_employee_id == Employee.id,
            ProfileChangeRequest.status == RequestStatus.APPROVED,
            ReviewDecision.via == ReviewVia.DIRECT,
        )
        .correlate(Employee)
        .exists()
    )
    records = (
        select(Evidence.id)
        .where(
            Evidence.org_id == Employee.org_id,
            Evidence.subject_employee_id == Employee.id,
            Evidence.kind == EvidenceKind.WORK_RECORD,
            Evidence.status == EvidenceStatus.ACTIVE,
        )
        .correlate(Employee)
        .exists()
    )
    source = (
        select(SourceConnection.id)
        .where(SourceConnection.org_id == Employee.org_id)
        .correlate(Employee)
        .exists()
    )
    imported = (
        select(Evidence.id)
        .where(
            Evidence.org_id == Employee.org_id,
            Evidence.kind == EvidenceKind.WORK_RECORD,
        )
        .correlate(Employee)
        .exists()
    )
    # A pending initial draft takes priority over later direct edits.
    # Ordinary supplements and publications do not imply initial owner review.
    return case(
        (pending, "pending_owner"),
        (handled, "owner_reviewed"),
        (direct_edited, "direct_edited"),
        (col(ProfileProgress.inference_status) == InferenceStatus.RUNNING, "drafting"),
        (col(ProfileProgress.inference_status) == InferenceStatus.FAILED, "failed"),
        (
            records
            & (
                (col(ProfileProgress.inference_status) == InferenceStatus.GENERATED)
                | initial.exists()
            ),
            "no_judgment",
        ),
        (records, "waiting_draft"),
        (source | imported, "no_records"),
        else_="waiting_source",
    )


def managed_scope(
    session: Session, org_id: UUID, department_ids: frozenset[UUID]
) -> frozenset[UUID]:
    if not department_ids:
        return frozenset()
    scope = (
        select(Department.id)
        .where(Department.org_id == org_id, col(Department.id).in_(department_ids))
        .cte("managed_scope", recursive=True)
    )
    # UNION deduplicates overlapping grants and terminates even with a hierarchy cycle.
    scope = scope.union(
        select(Department.id)
        .join(scope, col(Department.parent_department_id) == scope.c.id)
        .where(Department.org_id == org_id)
    )
    return frozenset(session.exec(select(scope.c.id)).all())


class EmployeeNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "未找到该员工或你没有权限查看")


def visible_employee(
    session: Session, principal: Principal, employee_id: UUID
) -> Employee:
    employee = session.exec(
        select(Employee).where(
            employee_scope_clause(principal), Employee.id == employee_id
        )
    ).first()
    if employee is None:
        raise EmployeeNotFound()
    return employee


def employee_summary(
    session: Session, employee: Employee, principal: Principal
) -> EmployeeSummary:
    managers = _reporting_managers(principal)
    row = session.exec(
        Select[
            tuple[
                Department | None,
                ProfileProgress | None,
                ProfileVersion | None,
                UUID | None,
                str | None,
                list[EmployeeField],
                str,
            ]
        ](
            Department,
            ProfileProgress,
            ProfileVersion,
            managers.c.id,
            managers.c.display_name,
            col(Organization.employee_fields),
            _initial_profile_status(),
        )
        .select_from(Employee)
        .join(Organization, col(Organization.id) == Employee.org_id)
        .outerjoin(managers, col(Employee.id) == managers.c.employee_id)
        .outerjoin(
            Department,
            (col(Department.org_id) == Employee.org_id)
            & (col(Department.id) == Employee.department_id),
        )
        .outerjoin(
            ProfileProgress,
            (col(ProfileProgress.org_id) == Employee.org_id)
            & (col(ProfileProgress.employee_id) == Employee.id),
        )
        .outerjoin(
            ProfileVersion,
            (col(ProfileVersion.org_id) == Employee.org_id)
            & (col(ProfileVersion.subject_employee_id) == Employee.id),
        )
        .where(col(Employee.org_id) == employee.org_id, col(Employee.id) == employee.id)
        .order_by(col(ProfileVersion.revision).desc())
        .limit(1)
    ).one()
    department, progress, version, manager_id, manager_name, fields, initial = row
    return _employee_summary(
        employee,
        department,
        progress,
        version,
        fields,
        review_statuses(
            session, employee.org_id, [employee.id], {employee.id} if version else set()
        )[employee.id],
        manager_id,
        manager_name,
        initial,
    )


def _employee_summary(
    employee: Employee,
    department: Department | None,
    progress: ProfileProgress | None,
    version: ProfileVersion | None,
    fields: list[EmployeeField],
    review_status: ProgressReviewStatus,
    manager_id: UUID | None,
    manager_name: str | None,
    initial_status: str,
) -> EmployeeSummary:
    return EmployeeSummary(
        **{f: getattr(employee, f) for f in fields},
        **employee.model_dump(
            include={
                "id",
                "display_name",
                "title",
                "employment_status",
                "employee_code",
            }
        ),
        department=DepartmentSummary.model_validate(department) if department else None,
        progress=ProgressPublic(
            **(
                progress
                or ProfileProgress(org_id=employee.org_id, employee_id=employee.id)
            ).model_dump(),
            review_status=review_status,
            initial_profile_status=InitialProfileStatus(initial_status),
        ),
        latest_published=PublishedSummary.model_validate(version) if version else None,
        manager=EmployeeReference(id=manager_id, display_name=manager_name)
        if manager_id and manager_name is not None
        else None,
    )


def list_employees(
    session: Session,
    principal: Principal,
    *,
    department_id: UUID | None,
    q: str | None,
    skip: int,
    limit: int,
    employment_status: EmploymentStatus | None = None,
) -> EmployeesPublic:
    filters = [employee_scope_clause(principal)]
    if employment_status is not None:
        filters.append(col(Employee.employment_status) == employment_status)
    if department_id is not None:
        filters.append(col(Employee.department_id) == department_id)
    if q:
        filters.append(col(Employee.display_name).icontains(q, autoescape=True))
    count, fields = session.exec(
        select(
            func.count(),
            select(col(Organization.employee_fields))
            .where(Organization.id == principal.org_id)
            .scalar_subquery(),
        )
        .select_from(Employee)
        .where(*filters)
    ).one()
    latest_version = aliased(
        ProfileVersion,
        select(ProfileVersion)
        .where(ProfileVersion.org_id == principal.org_id)
        .distinct(col(ProfileVersion.subject_employee_id))
        .order_by(
            col(ProfileVersion.subject_employee_id), col(ProfileVersion.revision).desc()
        )
        .subquery(),
    )
    managers = _reporting_managers(principal)
    rows = session.exec(
        Select[
            tuple[
                Employee,
                Department | None,
                ProfileProgress | None,
                ProfileVersion | None,
                UUID | None,
                str | None,
                str,
            ]
        ](
            Employee,
            Department,
            ProfileProgress,
            latest_version,
            managers.c.id,
            managers.c.display_name,
            _initial_profile_status(),
        )
        .where(*filters)
        .outerjoin(managers, col(Employee.id) == managers.c.employee_id)
        .outerjoin(
            Department,
            (col(Department.org_id) == Employee.org_id)
            & (col(Department.id) == Employee.department_id),
        )
        .outerjoin(
            ProfileProgress,
            (col(ProfileProgress.org_id) == Employee.org_id)
            & (col(ProfileProgress.employee_id) == Employee.id),
        )
        .outerjoin(
            latest_version,
            (col(latest_version.org_id) == Employee.org_id)
            & (col(latest_version.subject_employee_id) == Employee.id),
        )
        .order_by(Employee.display_name, col(Employee.id))
        .offset(skip)
        .limit(limit)
    ).all()
    statuses = review_statuses(
        session,
        principal.org_id,
        [e.id for e, *_ in rows],
        {e.id for e, _, _, version, *_ in rows if version},
    )
    return EmployeesPublic(
        data=[
            _employee_summary(
                e,
                d,
                p,
                v,
                fields,
                statuses[e.id],
                manager_id,
                manager_name,
                initial_status,
            )
            for e, d, p, v, manager_id, manager_name, initial_status in rows
        ],
        count=count,
    )


def rename_organization(
    session: Session, principal: Principal, name: str, *, commit: bool = True
) -> OrganizationPublic:
    organization = session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    old_name = organization.name
    organization.name = name
    session.add(organization)
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="org.renamed",
        target_type="organization",
        target_id=str(principal.org_id),
        details={"old_name": old_name, "new_name": name},
    )
    if commit:
        session.commit()
        session.refresh(organization)
    else:
        session.flush()
    return OrganizationPublic.model_validate(organization)
