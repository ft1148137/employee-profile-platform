"""Tenant-scoped holds and atomic commitments. Callers own the transaction."""

from collections import defaultdict
from datetime import date, timedelta
from uuid import UUID

from fastapi import HTTPException
from pydantic import JsonValue
from sqlmodel import Session, col, select

from app.authz.deps import get_org_principal
from app.authz.policies import can_view_employee, employee_scope_clause
from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capacity import calendar
from app.domains.capacity.demand import (
    demand_row,
    get_demand,
    get_demand_basis,
    represents_department,
    responder_user_ids,
)
from app.domains.capacity.demand_schemas import (
    DemandPublic,
    ProposalApprovalPublic,
    ProposalDetail,
    ProposalLineInput,
    ProposalLinePublic,
    ProposalSummary,
)
from app.domains.capacity.models import (
    AssignmentStatus,
    CapacityApproval,
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityProposal,
    CapacityProposalLine,
    DemandEventKind,
    DemandStatus,
    ProposalStatus,
)
from app.domains.capacity.schemas import Via
from app.domains.capacity.settings import CapacityForbidden, CapacityNotFound, audit
from app.domains.capacity.workload import notify_employee
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.service import add_item
from app.domains.org.models import Employee, EmploymentStatus
from app.domains.org.service import managed_scope
from app.models import User

HOLD_DURATION = timedelta(hours=72)


class ProposalConflict(HTTPException):
    def __init__(self, message: str, **details: JsonValue) -> None:
        super().__init__(409, {"message": message, **details})


class ProposalFailed(ProposalConflict):
    """A known recheck failure; the REST boundary must commit before returning 409."""


class ProposalOwnershipError(ValueError):
    pass


class ProposalSelfApprovalForbidden(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "不能审批自己提交的方案")


def latest(
    session: Session, org_id: UUID, demand_id: UUID, *, lock: bool = False
) -> CapacityProposal | None:
    query = (
        select(CapacityProposal)
        .where(
            CapacityProposal.org_id == org_id,
            CapacityProposal.demand_id == demand_id,
        )
        .order_by(
            col(CapacityProposal.created_at).desc(), col(CapacityProposal.id).desc()
        )
        .limit(1)
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return session.exec(query).first()


def lines_for(session: Session, row: CapacityProposal) -> list[CapacityProposalLine]:
    return list(
        session.exec(
            select(CapacityProposalLine)
            .where(
                CapacityProposalLine.org_id == row.org_id,
                CapacityProposalLine.proposal_id == row.id,
            )
            .order_by(
                col(CapacityProposalLine.employee_id),
                col(CapacityProposalLine.start_date),
                col(CapacityProposalLine.id),
            )
        ).all()
    )


def decided(row: CapacityProposal) -> ProposalConflict:
    return ProposalConflict(
        "方案已处理，请刷新后查看", code="proposal_decided", status=row.status
    )


def check_pending(row: CapacityProposal, revision: int, expected: int) -> None:
    if row.status != ProposalStatus.PENDING or revision != expected:
        raise decided(row)


def conflict_sources(
    principal: Principal,
    session: Session,
    employee_id: UUID,
    week: date,
    exclude: UUID | None,
) -> list[str]:
    end = week + timedelta(days=6)
    assigned = session.exec(
        select(CapacityAssignment.demand_id).where(
            CapacityAssignment.org_id == principal.org_id,
            CapacityAssignment.employee_id == employee_id,
            CapacityAssignment.status == AssignmentStatus.ACTIVE,
            CapacityAssignment.due_date >= week,
            col(CapacityAssignment.start_date).is_(None)
            | (col(CapacityAssignment.start_date) <= end),
        )
    ).all()
    held = session.exec(
        select(CapacityProposal.demand_id)
        .join(
            CapacityProposalLine,
            (col(CapacityProposalLine.org_id) == CapacityProposal.org_id)
            & (col(CapacityProposalLine.proposal_id) == CapacityProposal.id),
        )
        .where(
            CapacityProposal.org_id == principal.org_id,
            CapacityProposalLine.employee_id == employee_id,
            CapacityProposal.status == ProposalStatus.PENDING,
            CapacityProposal.hold_expires_at > utc_now(),
            CapacityProposalLine.start_date <= end,
            CapacityProposalLine.end_date >= week,
            *([CapacityProposal.id != exclude] if exclude else []),
        )
    ).all()
    demands = session.exec(
        select(CapacityDemand).where(
            CapacityDemand.org_id == principal.org_id,
            col(CapacityDemand.id).in_(set(assigned) | set(held)),
        )
    ).all()
    return sorted(
        {
            d.description
            if (
                d.requester_user_id == principal.user_id
                or d.requesting_department_id in principal.managed_department_ids
                or represents_department(principal, d.target_department_id)
            )
            else "其他需求"
            for d in demands
        }
    )


def check_lines(
    principal: Principal,
    session: Session,
    demand: CapacityDemand,
    lines: list[ProposalLineInput],
    *,
    exclude: UUID | None = None,
    conflict_viewer: Principal | None = None,
) -> None:
    if not lines:
        raise ProposalConflict("方案没有可安排的人员")
    # Every commitment writer takes these locks in UUID order, after its demand.
    people: dict[UUID, Employee] = {}
    for employee_id in sorted({line.employee_id for line in lines}):
        person = session.exec(
            select(Employee)
            .where(
                Employee.org_id == principal.org_id,
                Employee.id == employee_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if person is None or person.employment_status != EmploymentStatus.ACTIVE:
            raise ProposalConflict("方案人员已不在职，请重新规划")
        people[employee_id] = person
    target = managed_scope(
        session, principal.org_id, frozenset({demand.target_department_id})
    )
    basis = {
        p.employee_id for p in get_demand_basis(principal, session, demand.id).data
    }
    requested: dict[UUID, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    for line in lines:
        person = people[line.employee_id]
        if (
            not can_view_employee(principal, person)
            or person.department_id not in target
        ):
            raise ProposalConflict("方案人员已不在目标部门管辖范围内，请重新规划")
        if line.employee_id not in basis and not line.outside_basis:
            raise ProposalConflict("方案人员不在能力依据中，请确认按经理判断安排")
        week = max(
            calendar.week_start(line.start_date),
            calendar.this_week(session, principal.org_id),
        )
        while week <= calendar.week_start(line.end_date):
            requested[line.employee_id][week] += line.allocation_percent
            week += timedelta(weeks=1)
    for employee_id, amounts in requested.items():
        start, end = min(amounts), max(amounts)
        weekly = calendar.loads(
            session,
            principal.org_id,
            [employee_id],
            start,
            (end - start).days // 7 + 1,
            exclude_proposal_id=exclude,
        )[employee_id]
        unconfirmed: list[JsonValue] = []
        over: list[JsonValue] = []
        for load in weekly:
            if load.week_start not in amounts:
                continue
            if load.status != "confirmed":
                unconfirmed.append(
                    {"week_start": load.week_start.isoformat(), "status": load.status}
                )
            excess = (
                (load.other_percent or 0)
                + load.platform_percent
                + amounts[load.week_start]
                - load.capacity_percent
            )
            if excess > 0:
                over.append(
                    {
                        "week_start": load.week_start.isoformat(),
                        "over_percent": excess,
                        "sources": list[JsonValue](
                            conflict_sources(
                                conflict_viewer or principal,
                                session,
                                employee_id,
                                load.week_start,
                                exclude,
                            )
                        ),
                    }
                )
        if unconfirmed or over:
            raise ProposalConflict(
                "请先确认这些周的容量" if unconfirmed else "这些周的安排超过可用容量",
                weeks=unconfirmed,
                over_weeks=over,
            )


def create(
    principal: Principal,
    session: Session,
    demand: CapacityDemand,
    option_id: UUID,
    lines: list[ProposalLineInput],
) -> CapacityProposal:
    assert principal.user_id is not None
    row = CapacityProposal(
        org_id=principal.org_id,
        demand_id=demand.id,
        source_option_id=option_id,
        proposed_by_user_id=principal.user_id,
        demand_revision=demand.revision,
        hold_expires_at=utc_now() + HOLD_DURATION,
    )
    session.add(row)
    session.flush()
    session.add_all(
        [
            CapacityProposalLine(
                org_id=principal.org_id, proposal_id=row.id, **line.model_dump()
            )
            for line in lines
        ]
    )
    session.add(
        CapacityApproval(
            org_id=principal.org_id,
            proposal_id=row.id,
            party="resource_manager",
            decision="approved",
            user_id=principal.user_id,
            via="web",
        )
    )
    session.flush()
    return row


def public(
    principal: Principal, session: Session, demand: CapacityDemand
) -> ProposalDetail | ProposalSummary | None:
    row = latest(session, principal.org_id, demand.id)
    if row is None:
        return None
    lines = lines_for(session, row)
    approvals = session.exec(
        select(CapacityApproval)
        .where(
            CapacityApproval.org_id == principal.org_id,
            CapacityApproval.proposal_id == row.id,
        )
        .order_by(col(CapacityApproval.created_at), col(CapacityApproval.id))
    ).all()
    detailed = represents_department(principal, demand.target_department_id)
    reply = session.exec(
        select(CapacityDemandEvent)
        .where(
            CapacityDemandEvent.org_id == principal.org_id,
            CapacityDemandEvent.demand_id == demand.id,
            CapacityDemandEvent.kind == DemandEventKind.REPLIED,
        )
        .order_by(
            col(CapacityDemandEvent.created_at).desc(),
            col(CapacityDemandEvent.id).desc(),
        )
    ).first()
    summary = ProposalSummary(
        id=row.id,
        status=row.status,
        fail_reason=row.fail_reason,
        revision=row.revision,
        hold_expires_at=row.hold_expires_at,
        committed_at=row.committed_at,
        headcount=len({line.employee_id for line in lines if line.employee_id}),
        allocation_percents=sorted({line.allocation_percent for line in lines}),
        start_date=min(line.start_date for line in lines),
        end_date=max(line.end_date for line in lines),
        conditions=reply.text if reply else "",
        approvals=[
            ProposalApprovalPublic(
                party=a.party,
                decision=a.decision,
                created_at=a.created_at,
                reason=a.reason if detailed or a.user_id == principal.user_id else None,
            )
            for a in approvals
        ],
    )
    if not detailed:
        return summary
    people = {
        p.id: p
        for p in session.exec(
            select(Employee).where(
                employee_scope_clause(principal),
                col(Employee.id).in_({line.employee_id for line in lines}),
            )
        ).all()
    }
    return ProposalDetail(
        **summary.model_dump(),
        lines=[
            ProposalLinePublic(
                **line.model_dump(
                    exclude={
                        "org_id",
                        "created_at",
                        "proposal_id",
                        "lender_department_id",
                    }
                ),
                employee_name=people[line.employee_id].display_name,
            )
            for line in lines
            if line.employee_id is not None and line.employee_id in people
        ],
    )


def notify_parties(
    principal: Principal, session: Session, demand: CapacityDemand, message: str
) -> None:
    recipients = (
        {demand.requester_user_id}
        | responder_user_ids(session, principal.org_id, demand.requesting_department_id)
        | responder_user_ids(session, principal.org_id, demand.target_department_id)
    )
    for user_id in sorted(recipients):
        add_item(
            session,
            InboxItem(
                org_id=principal.org_id,
                recipient_user_id=user_id,
                kind=InboxKind.CAPACITY,
                message=message,
            ),
        )


def fail(
    principal: Principal,
    session: Session,
    demand: CapacityDemand,
    row: CapacityProposal,
    reason: str,
    message: str,
    *,
    via: Via = "web",
) -> None:
    row.status, row.fail_reason = ProposalStatus.FAILED, reason
    row.revision += 1
    demand.status = DemandStatus.SUBMITTED
    demand.committed_start_date = demand.committed_end_date = None
    demand.revision += 1
    session.add_all(
        [
            row,
            demand,
            CapacityDemandEvent(
                org_id=principal.org_id,
                demand_id=demand.id,
                kind=DemandEventKind.PROPOSAL_FAILED,
                actor_user_id=principal.user_id or row.proposed_by_user_id,
                text=message,
                via=via,
            ),
        ]
    )
    audit(
        principal,
        session,
        "capacity.demand_follow_up",
        "capacity_demand",
        demand.id,
        via,
        None,
        {"proposal_id": str(row.id), "fail_reason": reason},
    )
    notify_parties(principal, session, demand, message)
    session.flush()


def current_principal(session: Session, org_id: UUID, user_id: UUID) -> Principal:
    user = session.exec(
        select(User).where(User.id == user_id, User.is_active)
    ).one_or_none()
    if user is None:
        raise ProposalConflict("方案审批人的账号已停用")
    try:
        return get_org_principal(org_id, user, session)
    except HTTPException as error:
        if error.status_code != 404:
            raise
        raise ProposalConflict("方案审批人已不在本企业") from error


def expire_locked(
    principal: Principal,
    session: Session,
    demand: CapacityDemand,
    row: CapacityProposal,
    *,
    via: Via = "web",
) -> bool:
    if row.status != ProposalStatus.PENDING or row.hold_expires_at > utc_now():
        return False
    fail(
        principal, session, demand, row, "expired", "方案已过期，需要重新规划", via=via
    )
    return True


def assert_assignment_line(session: Session, assignment: CapacityAssignment) -> None:
    row = session.exec(
        select(CapacityProposalLine, CapacityProposal.demand_id)
        .join(
            CapacityProposal,
            (col(CapacityProposal.org_id) == CapacityProposalLine.org_id)
            & (col(CapacityProposal.id) == CapacityProposalLine.proposal_id),
        )
        .where(
            CapacityProposalLine.org_id == assignment.org_id,
            CapacityProposalLine.id == assignment.proposal_line_id,
        )
    ).one_or_none()
    if (
        row is None
        or row[1] != assignment.demand_id
        or row[0].employee_id != assignment.employee_id
    ):
        raise ProposalOwnershipError(
            "Assignment must belong to its proposal line's demand and employee"
        )


def accept(
    principal: Principal,
    session: Session,
    demand: CapacityDemand,
    row: CapacityProposal,
    *,
    via: Via,
) -> None:
    if expire_locked(principal, session, demand, row, via=via):
        raise ProposalFailed("方案已过期，请对方重新安排", code="proposal_expired")
    try:
        proposer = current_principal(session, principal.org_id, row.proposed_by_user_id)
        accepter = (
            current_principal(session, principal.org_id, principal.user_id)
            if principal.user_id
            else principal
        )
        if not represents_department(proposer, demand.target_department_id):
            raise ProposalConflict("提议人已没有目标部门的审批权限")
        if not (
            accepter.user_id == demand.requester_user_id
            or demand.requesting_department_id in accepter.managed_department_ids
        ):
            raise ProposalConflict("接受人已没有需求方审批权限")
        if (
            demand.revision != row.demand_revision
            or demand.status != DemandStatus.REPLIED
        ):
            raise ProposalConflict("需求版本已变化，请重新规划")
        lines = lines_for(session, row)
        if any(line.employee_id is None for line in lines):
            raise ProposalConflict("借调尚未完成审批")
        inputs = [ProposalLineInput.model_validate(line) for line in lines]
        check_lines(
            proposer, session, demand, inputs, exclude=row.id, conflict_viewer=accepter
        )
    except ProposalConflict as error:
        if expire_locked(principal, session, demand, row, via=via):
            raise ProposalFailed(
                "方案已过期，请对方重新安排", code="proposal_expired"
            ) from error
        fail(
            principal,
            session,
            demand,
            row,
            "recheck_failed",
            "方案复核未通过，需要重新规划",
            via=via,
        )
        # The accepter only receives department-level failure details.
        raise ProposalFailed(
            "方案复核未通过，请对方重新安排",
            code="proposal_recheck_failed",
            reason=error.detail,
        ) from error
    if expire_locked(principal, session, demand, row, via=via):
        raise ProposalFailed("方案已过期，请对方重新安排", code="proposal_expired")
    assert principal.user_id is not None
    session.add(
        CapacityApproval(
            org_id=principal.org_id,
            proposal_id=row.id,
            party="requester",
            decision="approved",
            user_id=principal.user_id,
            via=via,
        )
    )
    for line in lines:
        assert line.employee_id is not None
        assignment = CapacityAssignment(
            org_id=principal.org_id,
            demand_id=demand.id,
            employee_id=line.employee_id,
            proposal_line_id=line.id,
            allocation_percent=line.allocation_percent,
            start_date=line.start_date,
            due_date=line.end_date,
            assigned_by_user_id=row.proposed_by_user_id,
        )
        assert_assignment_line(session, assignment)
        session.add(assignment)
        audit(
            principal,
            session,
            "capacity.assignment_created",
            "capacity_assignment",
            assignment.id,
            via,
            None,
            {
                "proposal_id": str(row.id),
                "proposal_line_id": str(line.id),
                "outside_basis": line.outside_basis,
            },
        )
    row.status, row.committed_at = ProposalStatus.COMMITTED, utc_now()
    row.revision += 1
    demand.status = DemandStatus.ACCEPTED
    demand.revision += 1
    session.add_all([row, demand])
    for kind in (DemandEventKind.ACCEPTED, DemandEventKind.COMMITTED):
        session.add(
            CapacityDemandEvent(
                org_id=principal.org_id,
                demand_id=demand.id,
                actor_user_id=principal.user_id,
                kind=kind,
                text="",
                via=via,
            )
        )
    for employee_id in sorted({line.employee_id for line in lines if line.employee_id}):
        notify_employee(principal, session, employee_id)
    notify_parties(principal, session, demand, "方案已接受，安排已生效")
    audit(
        principal,
        session,
        "capacity.demand_accepted",
        "capacity_demand",
        demand.id,
        via,
        None,
        {"proposal_id": str(row.id)},
    )
    session.flush()


def respond(
    principal: Principal,
    session: Session,
    demand: CapacityDemand,
    row: CapacityProposal,
    action: str,
    revision: int,
    text: str | None,
    *,
    via: Via,
) -> DemandPublic:
    if via == "mcp":
        raise CapacityForbidden()
    if principal.user_id == row.proposed_by_user_id and action in (
        "accept",
        "follow_up",
    ):
        raise ProposalSelfApprovalForbidden()
    check_pending(row, revision, demand.revision)
    if action == "accept":
        accept(principal, session, demand, row, via=via)
    elif action == "follow_up":
        if not text or not text.strip() or len(text) > 500:
            raise ProposalConflict("拒绝方案时必须填写不超过 500 字的理由")
        assert principal.user_id is not None
        session.add(
            CapacityApproval(
                org_id=principal.org_id,
                proposal_id=row.id,
                party="requester",
                decision="rejected",
                user_id=principal.user_id,
                via=via,
                reason=text,
            )
        )
        fail(
            principal,
            session,
            demand,
            row,
            "rejected",
            f"方案被拒，需要重新规划：{text}",
            via=via,
        )
    else:
        withdraw_locked(principal, session, demand, row, withdraw_demand=True, via=via)
    return get_demand(principal, session, demand.id)


def withdraw_locked(
    principal: Principal,
    session: Session,
    demand: CapacityDemand,
    row: CapacityProposal,
    *,
    withdraw_demand: bool = False,
    via: Via = "web",
) -> None:
    assert principal.user_id is not None
    row.status, row.fail_reason = ProposalStatus.WITHDRAWN, None
    row.revision += 1
    demand.status = (
        DemandStatus.WITHDRAWN if withdraw_demand else DemandStatus.SUBMITTED
    )
    demand.committed_start_date = demand.committed_end_date = None
    demand.revision += 1
    session.add_all(
        [
            row,
            demand,
            CapacityDemandEvent(
                org_id=principal.org_id,
                demand_id=demand.id,
                actor_user_id=principal.user_id,
                kind=DemandEventKind.WITHDRAWN,
                text="需求已撤回" if withdraw_demand else "方案已撤回，需要重新规划",
                via=via,
            ),
        ]
    )
    audit(
        principal,
        session,
        "capacity.demand_withdrawn",
        "capacity_demand",
        demand.id,
        via,
        None,
        {"proposal_id": str(row.id), "withdraw_demand": withdraw_demand},
    )
    notify_parties(principal, session, demand, "方案已撤回，需要重新规划")
    session.flush()


def withdraw(
    principal: Principal, session: Session, proposal_id: UUID, revision: int
) -> DemandPublic:
    row = session.exec(
        select(CapacityProposal).where(
            CapacityProposal.org_id == principal.org_id,
            CapacityProposal.id == proposal_id,
        )
    ).one_or_none()
    if row is None:
        raise CapacityNotFound()
    demand = demand_row(principal, session, row.demand_id, lock=True)
    if not represents_department(principal, demand.target_department_id):
        raise CapacityNotFound()
    session.refresh(row, with_for_update=True)
    check_pending(row, revision, row.revision)
    withdraw_locked(principal, session, demand, row)
    return get_demand(principal, session, demand.id)


def expire(principal: Principal, session: Session) -> int:
    ids = session.exec(
        select(CapacityProposal.id, CapacityProposal.demand_id)
        .where(
            CapacityProposal.org_id == principal.org_id,
            CapacityProposal.status == ProposalStatus.PENDING,
            CapacityProposal.hold_expires_at <= utc_now(),
        )
        .order_by(col(CapacityProposal.demand_id), col(CapacityProposal.id))
    ).all()
    count = 0
    for proposal_id, demand_id in ids:
        # Use the same demand -> proposal lock order as accept and withdraw.
        demand = session.exec(
            select(CapacityDemand)
            .where(
                CapacityDemand.org_id == principal.org_id,
                CapacityDemand.id == demand_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        row = session.exec(
            select(CapacityProposal)
            .where(
                CapacityProposal.org_id == principal.org_id,
                CapacityProposal.id == proposal_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        if expire_locked(principal, session, demand, row):
            count += 1
    return count
