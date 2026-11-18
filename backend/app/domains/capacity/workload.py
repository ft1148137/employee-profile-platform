from datetime import date, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, func, or_, select

from app.authz.deps import get_org_principal
from app.authz.policies import can_view_employee, employee_scope_clause
from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capacity import calendar
from app.domains.capacity.models import (
    CapacityRequestStatus,
    CapacityUnavailability,
    CapacityWeek,
    CapacityWorkloadRequest,
)
from app.domains.capacity.schemas import (
    LastConfirmed,
    MyWorkload,
    UnavailabilityInput,
    UnavailabilityList,
    UnavailabilityPublic,
    Via,
    WorkloadDecisionInput,
    WorkloadList,
    WorkloadPublic,
    WorkloadRequestInput,
    WorkloadRequestList,
    WorkloadRequestPublic,
    WorkloadWeeksInput,
)
from app.domains.capacity.settings import (
    CapacityConflict,
    CapacityForbidden,
    CapacityNotFound,
    audit,
    require_enabled,
)
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.service import add_item
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    ExternalIdentity,
    OrgMembership,
)
from app.domains.review.routing import active_delegations, can_review, review_assignment
from app.domains.source.models import SourceConnection
from app.domains.work.models import Task
from app.models import User

AVAILABLE_BELOW = 80
NOTICE = "你有需求或负荷事项需要处理"
WORKLOAD_REVIEW_NOTICE = "有负荷调整申请等你审批"
EXTENSION_REVIEW_NOTICE = "有延期申请等你审批"


class WorkloadWeekExpired(HTTPException):
    def __init__(self) -> None:
        super().__init__(422, "结束周不能早于本周")


def validate_request_week(end_week: date, current: date) -> None:
    if end_week < current:
        raise WorkloadWeekExpired()


def employee(
    principal: Principal,
    session: Session,
    employee_id: UUID | None,
    *,
    lock: bool = False,
) -> Employee:
    query = select(Employee).where(
        Employee.org_id == principal.org_id, Employee.id == employee_id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.exec(query).one_or_none()
    if row is None or row.employment_status != EmploymentStatus.ACTIVE:
        raise CapacityNotFound()
    return row


def views(
    principal: Principal,
    session: Session,
    employees: list[Employee],
    from_week: date | None = None,
    weeks: int = 8,
) -> list[WorkloadPublic]:
    ids = [e.id for e in employees]
    org_id = principal.org_id
    current = calendar.this_week(session, org_id)
    start = from_week or current
    weekly = calendar.loads(session, org_id, ids, start, weeks)
    pending = {
        r.employee_id: r
        for r in session.exec(
            select(CapacityWorkloadRequest).where(
                CapacityWorkloadRequest.org_id == org_id,
                col(CapacityWorkloadRequest.employee_id).in_(ids),
                CapacityWorkloadRequest.status == CapacityRequestStatus.PENDING,
            )
        ).all()
    }
    current_loads = (
        weekly if start == current else calendar.loads(session, org_id, ids, current, 1)
    )
    latest = {
        record.employee_id: record
        for record in session.exec(
            select(CapacityWeek)
            .where(
                CapacityWeek.org_id == org_id, col(CapacityWeek.employee_id).in_(ids)
            )
            .distinct(col(CapacityWeek.employee_id))
            .order_by(
                col(CapacityWeek.employee_id), col(CapacityWeek.week_start).desc()
            )
        ).all()
    }
    # shortcut: Unsynced or stale unresolved tasks still count; revisit when Jira sync freshness is exposed.
    jira_connected = session.exec(
        select(
            select(SourceConnection.id)
            .where(SourceConnection.org_id == org_id, SourceConnection.kind == "jira")
            .exists()
        )
    ).one()
    jira = dict(
        session.exec(
            select(ExternalIdentity.employee_id, func.count(func.distinct(Task.id)))
            .outerjoin(
                Task,
                (col(Task.org_id) == ExternalIdentity.org_id)
                & (col(Task.source) == ExternalIdentity.source)
                & col(Task.attributes)["resolved"].astext.is_(None)
                & col(Task.attributes).contains(
                    func.jsonb_build_object(
                        "people",
                        func.jsonb_build_array(
                            func.jsonb_build_object(
                                "id", ExternalIdentity.external_id, "role", "assignee"
                            )
                        ),
                    )
                ),
            )
            .where(
                ExternalIdentity.org_id == org_id,
                jira_connected,
                ExternalIdentity.source == "jira",
                col(ExternalIdentity.employee_id).in_(ids),
            )
            .group_by(col(ExternalIdentity.employee_id))
        ).all()
    )
    departments = {
        d.id: d.name
        for d in session.exec(
            select(Department).where(Department.org_id == org_id)
        ).all()
    }
    result = []
    for e in employees:
        request = pending.get(e.id)
        load = current_loads[e.id][0]
        base, platform = load.other_percent, load.platform_percent
        last = latest.get(e.id)
        total = None if base is None else base + platform
        result.append(
            WorkloadPublic(
                employee_id=e.id,
                employee_name=e.display_name,
                department_id=e.department_id,
                department_name=departments.get(e.department_id)
                if e.department_id
                else None,
                jira_task_count=jira.get(e.id),
                base_percent=base,
                platform_percent=platform,
                confirmed_percent=total,
                band="unconfirmed"
                if load.status != "confirmed" or total is None
                else "available"
                if total < load.capacity_percent * AVAILABLE_BELOW // 100
                else "overloaded"
                if total > load.capacity_percent
                else "full",
                weeks=weekly[e.id],
                correction_pending={
                    w.week_start: request is not None
                    and (request.start_week or current)
                    <= w.week_start
                    <= (request.end_week or current)
                    for w in weekly[e.id]
                },
                last_confirmed=LastConfirmed(
                    percent=last.other_percent,
                    week_start=last.week_start,
                    confirmed_at=last.confirmed_at,
                )
                if last
                else None,
            )
        )
    return result


def get_my_workload(
    principal: Principal,
    session: Session,
    from_week: date | None = None,
    weeks: int = 8,
) -> MyWorkload:
    require_enabled(principal, session)
    subject = employee(principal, session, principal.employee_id)
    requests = session.exec(
        select(CapacityWorkloadRequest)
        .where(
            CapacityWorkloadRequest.org_id == principal.org_id,
            CapacityWorkloadRequest.employee_id == subject.id,
        )
        .order_by(
            col(CapacityWorkloadRequest.created_at).desc(),
            col(CapacityWorkloadRequest.id),
        )
    ).all()
    return MyWorkload(
        **views(principal, session, [subject], from_week, weeks)[0].model_dump(),
        requests=[WorkloadRequestPublic.model_validate(r) for r in requests],
        reviewer_names=workload_reviewer_names(
            principal, session, workload_reviewers(principal, session, subject.id)
        ),
    )


def list_workloads(
    principal: Principal,
    session: Session,
    department_id: UUID | None = None,
    from_week: date | None = None,
    weeks: int = 8,
    *,
    employee_name: str | None = None,
) -> WorkloadList:
    require_enabled(principal, session)
    if not principal.is_org_admin and not principal.managed_department_ids:
        raise CapacityForbidden()
    query = (
        select(Employee)
        .outerjoin(
            Department,
            (col(Department.org_id) == Employee.org_id)
            & (col(Department.id) == Employee.department_id),
        )
        .where(
            employee_scope_clause(principal),
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
    )
    if department_id is not None:
        if (
            not principal.is_org_admin
            and department_id not in principal.managed_department_ids
        ):
            raise CapacityNotFound()
        query = query.where(Employee.department_id == department_id)
    if employee_name is not None:
        query = query.where(
            func.lower(Employee.display_name) == func.lower(employee_name)
        )
    rows = list(
        session.exec(
            query.order_by(
                col(Department.name), col(Employee.display_name), col(Employee.id)
            )
        ).all()
    )
    data = views(principal, session, rows, from_week, weeks)
    return WorkloadList(data=data, count=len(data))


def notify_employee(principal: Principal, session: Session, employee_id: UUID) -> None:
    add_item(
        session,
        InboxItem(
            org_id=principal.org_id,
            recipient_employee_id=employee_id,
            kind=InboxKind.CAPACITY,
            message=NOTICE,
        ),
    )


def workload_reviewers(
    principal: Principal, session: Session, employee_id: UUID
) -> list[User]:
    managers, _ = review_assignment(session, principal.org_id, employee_id)
    delegates = active_delegations(session, principal.org_id, managers)
    recipients = set(managers) | {row.delegate_user_id for row in delegates}
    users = session.exec(
        select(User)
        .join(OrgMembership, col(OrgMembership.user_id) == User.id)
        .where(
            OrgMembership.org_id == principal.org_id,
            col(User.id).in_(recipients),
            User.is_active,
        )
        .order_by(col(User.id))
    ).all()
    return [
        user
        for user in users
        if user.id != principal.user_id
        and can_review(
            session,
            get_org_principal(principal.org_id, user, session),
            employee_id,
        )
        is not None
    ]


def workload_reviewer_names(
    principal: Principal, session: Session, users: list[User]
) -> list[str]:
    from app.domains.capacity.extension import user_names

    return list(
        user_names(session, principal.org_id, {user.id for user in users}).values()
    )


def set_week(
    principal: Principal, session: Session, employee_id: UUID, percent: int, start: date
) -> int | None:
    if principal.user_id is None:
        raise CapacityForbidden()
    row = session.exec(
        select(CapacityWeek).where(
            CapacityWeek.org_id == principal.org_id,
            CapacityWeek.employee_id == employee_id,
            CapacityWeek.week_start == start,
        )
    ).one_or_none()
    before = row.other_percent if row else None
    if row is None:
        row = CapacityWeek(
            org_id=principal.org_id,
            employee_id=employee_id,
            week_start=start,
            other_percent=percent,
            confirmed_by_user_id=principal.user_id,
        )
    row.other_percent, row.confirmed_at, row.confirmed_by_user_id = (
        percent,
        utc_now(),
        principal.user_id,
    )
    session.add(row)
    session.flush()
    return before


def set_weeks(
    principal: Principal,
    session: Session,
    body: WorkloadWeeksInput,
) -> WorkloadList:
    require_enabled(principal, session)
    subjects = []
    for item in sorted(body.items, key=lambda item: item.employee_id):
        subject = employee(principal, session, item.employee_id, lock=True)
        if not can_view_employee(principal, subject):
            raise CapacityNotFound()
        if (
            subject.id == principal.employee_id
            or subject.department_id not in principal.managed_department_ids
        ):
            raise CapacityForbidden()
        subjects.append(subject)
    for item in body.items:
        for offset in range((body.end_week - body.start_week).days // 7 + 1):
            before = set_week(
                principal,
                session,
                item.employee_id,
                item.other_percent,
                body.start_week + timedelta(weeks=offset),
            )
            audit(
                principal,
                session,
                "capacity.workload_adjusted",
                "employee",
                item.employee_id,
                "web",
                None,
                {
                    "before_percent": before,
                    "after_percent": item.other_percent,
                    "week_start": (
                        body.start_week + timedelta(weeks=offset)
                    ).isoformat(),
                },
            )
        notify_employee(principal, session, item.employee_id)
    data = views(principal, session, subjects)
    return WorkloadList(data=data, count=len(data))


def request_workload_change(
    principal: Principal,
    session: Session,
    requested_percent: int,
    reason: str,
    start_week: date,
    end_week: date,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> WorkloadRequestPublic:
    body = WorkloadRequestInput(
        requested_percent=requested_percent,
        reason=reason,
        start_week=start_week,
        end_week=end_week,
    )
    require_enabled(principal, session)
    subject = employee(principal, session, principal.employee_id, lock=True)
    validate_request_week(body.end_week, calendar.this_week(session, principal.org_id))
    pending = session.exec(
        select(CapacityWorkloadRequest.id).where(
            CapacityWorkloadRequest.org_id == principal.org_id,
            CapacityWorkloadRequest.employee_id == subject.id,
            CapacityWorkloadRequest.status == CapacityRequestStatus.PENDING,
        )
    ).first()
    if pending:
        raise CapacityConflict()
    row = CapacityWorkloadRequest(
        org_id=principal.org_id, employee_id=subject.id, **body.model_dump()
    )
    session.add(row)
    reviewers = workload_reviewers(principal, session, subject.id)
    for user in reviewers:
        add_item(
            session,
            InboxItem(
                org_id=principal.org_id,
                recipient_user_id=user.id,
                kind=InboxKind.CAPACITY,
                message=WORKLOAD_REVIEW_NOTICE,
            ),
        )
    base = session.exec(
        select(CapacityWeek.other_percent).where(
            CapacityWeek.org_id == principal.org_id,
            CapacityWeek.employee_id == subject.id,
            CapacityWeek.week_start == calendar.this_week(session, principal.org_id),
        )
    ).one_or_none()
    audit(
        principal,
        session,
        "capacity.workload_requested",
        "capacity_workload_request",
        row.id,
        via,
        client_name,
        {"before_percent": base, "after_percent": body.requested_percent},
    )
    result = WorkloadRequestPublic.model_validate(row)
    result.reviewer_names = workload_reviewer_names(principal, session, reviewers)
    return result


def list_workload_requests(
    principal: Principal, session: Session
) -> WorkloadRequestList:
    from app.domains.capacity.extension import user_names

    require_enabled(principal, session)
    query = (
        select(CapacityWorkloadRequest, Employee.display_name)
        .join(
            Employee,
            (col(Employee.org_id) == CapacityWorkloadRequest.org_id)
            & (col(Employee.id) == CapacityWorkloadRequest.employee_id),
        )
        .where(
            CapacityWorkloadRequest.org_id == principal.org_id,
            Employee.employment_status == EmploymentStatus.ACTIVE,
            or_(
                CapacityWorkloadRequest.status == CapacityRequestStatus.PENDING,
                CapacityWorkloadRequest.decided_by_user_id == principal.user_id,
                CapacityWorkloadRequest.decided_for_user_id == principal.user_id,
            ),
        )
        .order_by(
            col(CapacityWorkloadRequest.created_at).desc(),
            col(CapacityWorkloadRequest.id),
        )
    )
    rows = list(
        session.exec(
            query.where(CapacityWorkloadRequest.status == CapacityRequestStatus.PENDING)
        ).all()
    )
    rows.extend(
        session.exec(
            query.where(
                CapacityWorkloadRequest.status != CapacityRequestStatus.PENDING
            ).limit(100)
        ).all()
    )
    rows.sort(key=lambda r: (-r[0].created_at.timestamp(), r[0].id))
    reviewers: dict[UUID, UUID | None] = {}
    reviewer_names: dict[UUID, str] = {}
    data = []
    for row, employee_name in rows:
        if row.employee_id not in reviewers:
            reviewers[row.employee_id] = can_review(session, principal, row.employee_id)
        if reviewers[row.employee_id] is not None:
            reviewer = reviewers[row.employee_id]
            assert reviewer is not None
            if reviewer != principal.user_id and reviewer not in reviewer_names:
                reviewer_names[reviewer] = user_names(
                    session, principal.org_id, {reviewer}
                )[reviewer]
            data.append(
                WorkloadRequestPublic(
                    **row.model_dump(),
                    employee_name=employee_name,
                    on_behalf_of_name=reviewer_names[reviewer]
                    if reviewer != principal.user_id
                    else None,
                )
            )
    return WorkloadRequestList(data=data, count=len(data))


def decide_workload_request(
    principal: Principal,
    session: Session,
    request_id: UUID,
    body: WorkloadDecisionInput,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> WorkloadRequestPublic:
    require_enabled(principal, session)
    row = session.exec(
        select(CapacityWorkloadRequest).where(
            CapacityWorkloadRequest.org_id == principal.org_id,
            CapacityWorkloadRequest.id == request_id,
        )
    ).one_or_none()
    if row is None:
        raise CapacityNotFound()
    employee(principal, session, row.employee_id, lock=True)
    manager_id = can_review(session, principal, row.employee_id)
    if manager_id is None:
        raise CapacityNotFound()
    row = session.exec(
        select(CapacityWorkloadRequest)
        .where(
            CapacityWorkloadRequest.org_id == principal.org_id,
            CapacityWorkloadRequest.id == request_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if row.status != CapacityRequestStatus.PENDING:
        raise CapacityConflict()
    before = session.exec(
        select(CapacityWeek.other_percent).where(
            CapacityWeek.org_id == principal.org_id,
            CapacityWeek.employee_id == row.employee_id,
            CapacityWeek.week_start
            == (row.start_week or calendar.this_week(session, principal.org_id)),
        )
    ).one_or_none()
    if body.approve:
        start = row.start_week or calendar.this_week(session, principal.org_id)
        end = row.end_week or start
        for offset in range((end - start).days // 7 + 1):
            set_week(
                principal,
                session,
                row.employee_id,
                row.requested_percent,
                start + timedelta(weeks=offset),
            )
    row.status = (
        CapacityRequestStatus.APPROVED
        if body.approve
        else CapacityRequestStatus.RETURNED
    )
    row.decided_by_user_id, row.decided_for_user_id = (
        principal.user_id,
        manager_id if manager_id != principal.user_id else None,
    )
    row.decided_at, row.decision_note = utc_now(), body.note
    session.add(row)
    audit(
        principal,
        session,
        "capacity.workload_request_approved"
        if body.approve
        else "capacity.workload_request_returned",
        "capacity_workload_request",
        row.id,
        via,
        client_name,
        {
            "before_percent": before,
            "after_percent": row.requested_percent if body.approve else before,
        },
    )
    notify_employee(principal, session, row.employee_id)
    return WorkloadRequestPublic.model_validate(row)


def unavailability_public(
    principal: Principal,
    session: Session,
    row: CapacityUnavailability,
    subject: Employee,
) -> UnavailabilityPublic:
    start = calendar.week_start(row.start_date)
    weekly = calendar.loads(
        session,
        principal.org_id,
        [subject.id],
        start,
        (calendar.week_start(row.end_date) - start).days // 7 + 1,
    )[subject.id]
    return UnavailabilityPublic(
        **row.model_dump(exclude={"org_id"}),
        can_delete=not principal.is_org_admin
        and (
            row.created_by_user_id == principal.user_id
            or subject.department_id in principal.managed_department_ids
        ),
        over_weeks=[week for week, _ in calendar.shortfalls(weekly, 0)[1]],
    )


def list_unavailability(
    principal: Principal,
    session: Session,
    employee_id: UUID,
    from_date: date,
    to_date: date,
) -> UnavailabilityList:
    require_enabled(principal, session)
    subject = session.exec(
        select(Employee).where(
            employee_scope_clause(principal),
            Employee.id == employee_id,
        )
    ).one_or_none()
    if subject is None:
        raise CapacityNotFound()
    rows = session.exec(
        select(CapacityUnavailability)
        .where(
            CapacityUnavailability.org_id == principal.org_id,
            CapacityUnavailability.employee_id == employee_id,
            CapacityUnavailability.start_date <= to_date,
            CapacityUnavailability.end_date >= from_date,
        )
        .order_by(
            col(CapacityUnavailability.start_date), col(CapacityUnavailability.id)
        )
    ).all()
    data = [unavailability_public(principal, session, row, subject) for row in rows]
    return UnavailabilityList(data=data, count=len(data))


def register_unavailability(
    principal: Principal,
    session: Session,
    body: UnavailabilityInput,
) -> UnavailabilityPublic:
    require_enabled(principal, session)
    subject = employee(principal, session, body.employee_id, lock=True)
    if not can_view_employee(principal, subject):
        raise CapacityNotFound()
    if principal.is_org_admin:
        raise CapacityForbidden()
    if principal.user_id is None or (
        subject.id != principal.employee_id
        and subject.department_id not in principal.managed_department_ids
    ):
        raise CapacityForbidden()
    row = CapacityUnavailability(
        org_id=principal.org_id,
        created_by_user_id=principal.user_id,
        **body.model_dump(),
    )
    session.add(row)
    session.flush()
    audit(
        principal,
        session,
        "capacity.unavailability_registered",
        "capacity_unavailability",
        row.id,
        "web",
        None,
        body.model_dump(mode="json"),
    )
    if subject.id != principal.employee_id:
        notify_employee(principal, session, subject.id)
    return unavailability_public(principal, session, row, subject)


def delete_unavailability(
    principal: Principal,
    session: Session,
    unavailability_id: UUID,
) -> UnavailabilityPublic:
    require_enabled(principal, session)
    query = select(CapacityUnavailability).where(
        CapacityUnavailability.org_id == principal.org_id,
        CapacityUnavailability.id == unavailability_id,
    )
    row = session.exec(query).one_or_none()
    if row is None:
        raise CapacityNotFound()
    subject = employee(principal, session, row.employee_id, lock=True)
    row = session.exec(query.execution_options(populate_existing=True)).one_or_none()
    if row is None or not can_view_employee(principal, subject):
        raise CapacityNotFound()
    if principal.is_org_admin:
        raise CapacityForbidden()
    if (
        row.created_by_user_id != principal.user_id
        and subject.department_id not in principal.managed_department_ids
    ):
        raise CapacityForbidden()
    session.delete(row)
    session.flush()
    audit(
        principal,
        session,
        "capacity.unavailability_deleted",
        "capacity_unavailability",
        row.id,
        "web",
        None,
        {
            "employee_id": str(subject.id),
            "start_date": row.start_date.isoformat(),
            "end_date": row.end_date.isoformat(),
        },
    )
    if subject.id != principal.employee_id:
        notify_employee(principal, session, subject.id)
    return unavailability_public(principal, session, row, subject)
