"""Suggest a previous manager without restoring reporting lines automatically."""

from uuid import UUID

from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.account.service import require_admin
from app.domains.org.models import Employee, EmploymentStatus, ReportingLine
from app.domains.org.roster.parser import validate_roster
from app.domains.org.roster.schemas import ParsedRoster
from app.domains.org.roster.single import current_roster
from app.domains.org.service import visible_employee


class PreviousManager(BaseModel):
    id: UUID
    display_name: str
    is_active: bool
    can_restore: bool


def previous_manager(
    db: Session,
    p: Principal,
    employee_id: UUID,
    roster: ParsedRoster | None = None,
) -> PreviousManager | None:
    require_admin(p)
    employee = visible_employee(db, p, employee_id)
    manager = db.exec(
        select(Employee)
        .join(ReportingLine, col(ReportingLine.manager_employee_id) == Employee.id)
        .where(
            Employee.org_id == p.org_id,
            ReportingLine.org_id == p.org_id,
            ReportingLine.employee_id == employee.id,
            col(ReportingLine.valid_to).is_not(None),
        )
        .order_by(
            col(ReportingLine.valid_to).desc(),
            col(ReportingLine.valid_from).desc(),
            col(ReportingLine.id).desc(),
        )
        .limit(1)
    ).first()
    if manager is None:
        return None
    candidate = (roster or current_roster(db, p)).model_copy(deep=True)
    by_code = {r.employee_code.casefold(): r for r in candidate.rows}
    manager_row = by_code.get((manager.employee_code or "").casefold())
    employee_row = by_code.get((employee.employee_code or "").casefold())
    is_active = (
        manager.employment_status == EmploymentStatus.ACTIVE
        if roster is None
        else manager_row is not None and not manager_row.inactive
    )
    can_restore = False
    if is_active and manager_row is not None and employee_row is not None:
        employee_row.inactive = False
        employee_row.manager_code = manager.employee_code
        candidate = candidate.active()
        validate_roster(candidate)
        can_restore = not any(
            issue.severity == "error" and issue.column == "上级工号"
            for issue in candidate.issues
        )
    return PreviousManager(
        id=manager.id,
        display_name=manager.display_name,
        is_active=is_active,
        can_restore=can_restore,
    )
