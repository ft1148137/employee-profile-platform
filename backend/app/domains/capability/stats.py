from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.schemas import CapabilityStats, CapabilityStatsPublic
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.org.models import Employee, EmploymentStatus
from app.domains.profile.models import AssessmentStatus, ProfileVersion
from app.domains.profile.schemas import SnapshotClaim


class CapabilityStatsForbidden(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "需要企业管理员或部门经理权限")


def read_stats(session: Session, principal: Principal) -> CapabilityStatsPublic:
    if not principal.is_org_admin and not principal.managed_department_ids:
        raise CapabilityStatsForbidden()
    schema = get_schema(session, principal.org_id)
    counts = {
        c.key: CapabilityStats(
            capability_key=c.key,
            employee_count=0,
            levels={},
            statuses=dict.fromkeys(AssessmentStatus, 0),
        )
        for c in list_capabilities(session, principal.org_id, schema.id)
    }
    # Frozen versions can reference definitions from retired dictionaries.
    keys = dict(
        session.exec(
            select(CapabilityDefinition.id, CapabilityDefinition.key).where(
                CapabilityDefinition.org_id == principal.org_id
            )
        ).all()
    )
    snapshots = session.exec(
        select(ProfileVersion.snapshot)
        .join(
            Employee,
            (col(Employee.id) == ProfileVersion.subject_employee_id)
            & (col(Employee.org_id) == ProfileVersion.org_id),
        )
        .where(
            ProfileVersion.org_id == principal.org_id,
            employee_scope_clause(principal),
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
        .distinct(col(ProfileVersion.subject_employee_id))
        .order_by(
            col(ProfileVersion.subject_employee_id), col(ProfileVersion.revision).desc()
        )
    )
    for snapshot in snapshots:
        raw = snapshot.get("claims", [])
        claims = (
            [SnapshotClaim.model_validate(c) for c in raw]
            if isinstance(raw, list)
            else []
        )
        by_key = {keys.get(c.capability_definition_id): c for c in claims}
        for key, claim in by_key.items():
            if key not in counts:
                continue
            stats = counts[key]
            stats.employee_count += 1
            level = claim.level if claim.level is not None else "_none"
            stats.levels[level] = stats.levels.get(level, 0) + 1
            stats.statuses[claim.assessment_status] += 1
    return CapabilityStatsPublic(data=list(counts.values()))
