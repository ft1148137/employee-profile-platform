from collections.abc import Callable

from pydantic import Field, JsonValue
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.capacity import workload
from app.domains.capacity.schemas import WorkloadListQuery
from app.domains.capacity.settings import CapacityNotFound, require_enabled

NOT_FOUND = "不在你的管辖范围或没有找到这个人"
DISABLED = "规划与负荷功能未开启，如需使用请联系企业管理员。"


class NamedWorkloadQuery(WorkloadListQuery):
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")


def read(
    db: Session,
    p: Principal,
    query: NamedWorkloadQuery,
    on_read: Callable[[set[str]], None] | None,
) -> JsonValue:
    name = query.name.strip()
    try:
        require_enabled(p, db)
    except CapacityNotFound:
        return {"error": DISABLED}
    try:
        if p.is_org_admin or p.managed_department_ids:
            rows = workload.list_workloads(
                p,
                db,
                query.department_id,
                query.from_week,
                query.weeks,
                employee_name=name,
            ).data
        else:
            subject = workload.employee(p, db, p.employee_id)
            rows = (
                workload.views(p, db, [subject], query.from_week, query.weeks)
                if name.casefold() == subject.display_name.casefold()
                and query.department_id in (None, subject.department_id)
                else []
            )
    except CapacityNotFound:
        return {"error": NOT_FOUND}
    if on_read:
        on_read({str(row.employee_id) for row in rows})
    if not rows:
        return {"error": NOT_FOUND}
    if len(rows) > 1:
        return {"error": "有多位同名员工，请补充部门后再查"}
    row = rows[0]
    return {
        "employee_name": row.employee_name,
        "department_name": row.department_name,
        "weeks": [
            week.model_dump(
                mode="json",
                include={"week_start", "other_percent", "platform_percent", "status"},
            )
            for week in row.weeks
        ],
    }
