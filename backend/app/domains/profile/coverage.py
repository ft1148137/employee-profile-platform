from collections import Counter
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.admin.overview import REVIEW_BACKLOG_DAYS
from app.domains.base import utc_now
from app.domains.change_request.models import (
    ChangeItem,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.change_request.service import review_statuses
from app.domains.org.models import Department, Employee, EmploymentStatus
from app.domains.profile.models import ProfileVersion, ProgressReviewStatus
from app.domains.review.policy import read_policy
from app.domains.source.models import SourceConnection, SyncRun


class CoverageStatus(BaseModel):
    status: ProgressReviewStatus
    count: int
    percentage: float


class CoverageSummary(BaseModel):
    total: int
    statuses: list[CoverageStatus]


class DepartmentCoverage(CoverageSummary):
    department_id: UUID
    department_name: str
    with_descendants: CoverageSummary | None


class JiraSyncFailure(BaseModel):
    connection_name: str
    failed_at: datetime


class CoveragePublic(CoverageSummary):
    departments: list[DepartmentCoverage]
    overdue_initial_requests: int
    review_backlog_requests: int
    jira_sync_failure: JiraSyncFailure | None


class CoverageForbidden(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "需要企业管理员或部门经理权限")


def _summary(statuses: list[ProgressReviewStatus]) -> CoverageSummary:
    counts = Counter(statuses)
    total = len(statuses)
    return CoverageSummary(
        total=total,
        statuses=[
            CoverageStatus(
                status=status,
                count=counts[status],
                percentage=round(counts[status] * 100 / total, 1) if total else 0,
            )
            for status in ProgressReviewStatus
        ],
    )


def read_coverage(session: Session, principal: Principal) -> CoveragePublic:
    if not principal.is_org_admin and not principal.managed_department_ids:
        raise CoverageForbidden()
    org_id, now = principal.org_id, utc_now()
    employees = session.exec(
        select(Employee).where(
            employee_scope_clause(principal),
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
    ).all()
    employee_ids = [e.id for e in employees]
    published_ids = set(
        session.exec(
            select(ProfileVersion.subject_employee_id).where(
                ProfileVersion.org_id == org_id,
                col(ProfileVersion.subject_employee_id).in_(employee_ids),
            )
        ).all()
    )
    statuses = review_statuses(session, org_id, employee_ids, published_ids)
    departments = session.exec(
        select(Department)
        .where(
            Department.org_id == org_id,
            True
            if principal.is_org_admin
            else col(Department.id).in_(
                principal.managed_department_ids
                | {e.department_id for e in employees if e.department_id}
            ),
        )
        .order_by(Department.name, col(Department.id))
    ).all()
    by_department: dict[UUID, list[ProgressReviewStatus]] = {
        d.id: [] for d in departments
    }
    for employee in employees:
        if (
            employee.department_id is not None
            and employee.department_id in by_department
        ):
            by_department[employee.department_id].append(statuses[employee.id])
    parent_ids = {d.id: d.parent_department_id for d in departments}
    subtree_statuses = {id: list(values) for id, values in by_department.items()}
    nonleaf = set()
    for department in departments:
        ancestor = department.parent_department_id
        seen = {department.id}
        while ancestor is not None and ancestor in parent_ids and ancestor not in seen:
            seen.add(ancestor)
            nonleaf.add(ancestor)
            subtree_statuses[ancestor].extend(by_department[department.id])
            ancestor = parent_ids[ancestor]
    requests = session.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == org_id,
            col(ProfileChangeRequest.subject_employee_id).in_(employee_ids),
            col(ProfileChangeRequest.status).in_(
                [RequestStatus.PENDING_OWNER, RequestStatus.PENDING_REVIEW]
            ),
        )
    ).all()
    nonempty = set(
        session.exec(
            select(ChangeItem.request_id).where(
                ChangeItem.org_id == org_id,
                col(ChangeItem.request_id).in_([r.id for r in requests]),
            )
        ).all()
    )
    opted_out = {e.id for e in employees if e.auto_draft_opt_out}
    deadline = now - timedelta(days=read_policy(session, org_id).owner_review_days)
    failure = None
    if principal.is_org_admin:
        # Match overview's latest-run rule; never expose config, errors or secrets.
        latest = session.exec(
            select(SyncRun, SourceConnection)
            .join(
                SourceConnection,
                (col(SourceConnection.org_id) == SyncRun.org_id)
                & (col(SourceConnection.id) == SyncRun.connection_id),
            )
            .where(SyncRun.org_id == org_id, SourceConnection.kind == "jira")
            .order_by(col(SyncRun.created_at).desc(), col(SyncRun.id).desc())
            .limit(1)
        ).first()
        if latest and latest[0].status == "failed":
            run, connection = latest
            failure = JiraSyncFailure(
                connection_name="Jira Cloud"
                if connection.config.get("deployment") == "cloud"
                else "Jira Data Center",
                failed_at=run.finished_at or run.created_at,
            )
    return CoveragePublic(
        **_summary(list(statuses.values())).model_dump(),
        departments=[
            DepartmentCoverage(
                department_id=d.id,
                department_name=d.name,
                **_summary(by_department[d.id]).model_dump(),
                with_descendants=_summary(subtree_statuses[d.id])
                if d.id in nonleaf
                else None,
            )
            for d in departments
        ],
        overdue_initial_requests=sum(
            r.origin == RequestOrigin.INITIAL_SYNC
            and r.status == RequestStatus.PENDING_OWNER
            and r.created_at <= deadline
            and r.id in nonempty
            and r.subject_employee_id not in opted_out
            for r in requests
        ),
        review_backlog_requests=sum(
            r.status == RequestStatus.PENDING_REVIEW
            and r.submitted_at is not None
            and r.submitted_at < now - timedelta(days=REVIEW_BACKLOG_DAYS)
            for r in requests
        ),
        jira_sync_failure=failure,
    )
