from sqlalchemy import and_, or_, true
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col

from app.authz.principal import Principal
from app.domains.audit.models import ActorKind
from app.domains.evidence.models import Visibility
from app.domains.org.models import Employee


def can_view_employee(p: Principal, employee: Employee) -> bool:
    return p.org_id == employee.org_id and (
        p.actor_type == ActorKind.SYSTEM_WORKER
        or p.is_org_admin
        or p.employee_id == employee.id
        or employee.department_id in p.managed_department_ids
    )


def employee_scope_clause(p: Principal) -> ColumnElement[bool]:
    scope = (
        true()
        if p.is_org_admin or p.actor_type == ActorKind.SYSTEM_WORKER
        else or_(
            col(Employee.id) == p.employee_id if p.employee_id else False,
            col(Employee.department_id).in_(p.managed_department_ids),
        )
    )
    return and_(col(Employee.org_id) == p.org_id, scope)


def can_view_evidence_content(
    p: Principal, *, subject_employee: Employee, visibility: Visibility
) -> bool:
    if not can_view_employee(p, subject_employee):
        return False
    if visibility == Visibility.STANDARD:
        return True
    return visibility == Visibility.RESTRICTED and (
        p.actor_type == ActorKind.SYSTEM_WORKER
        or p.employee_id == subject_employee.id
        or subject_employee.department_id in p.managed_department_ids
    )
