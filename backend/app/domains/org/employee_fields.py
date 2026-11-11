"""Fixed employee attributes are maintained independently of profile claims."""

from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.account.service import lock_org
from app.domains.audit.service import record_audit
from app.domains.org.models import EmployeeField

FIELD_LABELS: dict[EmployeeField, str] = {
    "job_family": "岗位序列",
    "work_location": "工作地点",
    "hire_date": "入职日期",
}


class EmployeeFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: list[EmployeeField]


def save_employee_fields(
    db: Session, p: Principal, args: EmployeeFields, *, commit: bool = True
) -> EmployeeFields:
    org = lock_org(db, p)
    fields = [field for field in FIELD_LABELS if field in args.fields]
    if org.employee_fields != fields:
        before = list(org.employee_fields)
        org.employee_fields = fields
        org.roster_revision += 1
        db.add(org)
        record_audit(
            db,
            org_id=p.org_id,
            actor_type=p.actor_type,
            actor_id=str(p.user_id),
            action="org.employee_fields_updated",
            target_type="organization",
            target_id=str(p.org_id),
            details={"before": [*before], "after": [*fields]},
        )
    if commit:
        db.commit()
    else:
        db.flush()
    return EmployeeFields(fields=fields)
