"""Tenant-scoped holds and atomic commitments. Callers own the transaction."""

from collections import defaultdict
from datetime import date, timedelta
from typing import cast
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
    department,
    get_demand,
    get_demand_basis,
    qualified_people,
    represents_department,
    responder_user_ids,
    validate_department_text,
    validate_load_words,
)
from app.domains.capacity.demand_schemas import (
    BorrowCandidate,
    BorrowDecisionInput,
    BorrowLineInput,
    BorrowRequest,
    ChangeProposalInput,
    ChangeProposalParticipant,
    CommitmentArrangement,
    DemandPublic,
    ParticipantChangeStatus,
    ParticipantPublic,
    PlanReplyInput,
    ProposalApprovalPublic,
    ProposalDetail,
    ProposalLineInput,
    ProposalLinePublic,
    ProposalParticipant,
    ProposalSegment,
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
from app.domains.capacity.settings import (
    CapacityForbidden,
    CapacityNotFound,
    audit,
    require_enabled,
)
from app.domains.capacity.workload import notify_employee
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.service import add_item
from app.domains.org.models import Employee, EmploymentStatus, OrgMembership
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
    session: Session,
    org_id: UUID,
    demand_id: UUID,
    *,
    lock: bool = False,
    status: ProposalStatus | None = None,
) -> CapacityProposal | None:
    query = (
        select(CapacityProposal)
        .where(
            CapacityProposal.org_id == org_id,
            CapacityProposal.demand_id == demand_id,
            *([CapacityProposal.status == status] if status is not None else []),
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


def active_segments(
    session: Session, org_id: UUID, demand_id: UUID, proposal_id: UUID
) -> list[tuple[CapacityAssignment, CapacityProposalLine]]:
    return list(
        session.exec(
            select(CapacityAssignment, CapacityProposalLine)
            .join(
                CapacityProposalLine,
                (col(CapacityAssignment.org_id) == CapacityProposalLine.org_id)
                & (col(CapacityAssignment.proposal_line_id) == CapacityProposalLine.id),
            )
            .where(
                CapacityAssignment.org_id == org_id,
                CapacityAssignment.demand_id == demand_id,
                CapacityAssignment.status == AssignmentStatus.ACTIVE,
                CapacityProposalLine.proposal_id == proposal_id,
            )
            .order_by(col(CapacityAssignment.id))
        ).all()
    )


def decided(row: CapacityProposal) -> ProposalConflict:
    return ProposalConflict(
        "方案已处理，请刷新后查看", code="proposal_decided", status=row.status
    )


def check_pending(row: CapacityProposal, revision: int, expected: int) -> None:
    if row.status != ProposalStatus.PENDING or revision != expected:
        raise decided(row)


def reply_superseded(demand: CapacityDemand, row: CapacityProposal) -> bool:
    # Ending a proposal advances the demand once; a new reply advances it again.
    return (
        row.kind == "initial"
        and row.status in (ProposalStatus.FAILED, ProposalStatus.WITHDRAWN)
        and demand.revision > row.demand_revision + 1
    )


def conflict_sources(
    principal: Principal,
    session: Session,
    employee_id: UUID,
    week: date,
    exclude: UUID | None,
    replace_demand: UUID | None,
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
            if d.id != replace_demand
        }
    )


def check_lines(
    principal: Principal,
    session: Session,
    demand: CapacityDemand,
    lines: list[ProposalLineInput],
    *,
    exclude: UUID | None = None,
    replace: bool = False,
    conflict_viewer: Principal | None = None,
    lender_department_id: UUID | None = None,
    lenders: dict[UUID, Principal] | None = None,
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
    target_id = lender_department_id or demand.target_department_id
    target = managed_scope(session, principal.org_id, frozenset({target_id}))
    basis = (
        set(qualified_people(principal, session, demand, target_id))
        if lender_department_id is not None
        else {
            p.employee_id for p in get_demand_basis(principal, session, demand.id).data
        }
    )
    requested: dict[UUID, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    for line in lines:
        person = people[line.employee_id]
        if (
            not can_view_employee(
                lenders[line.employee_id] if lenders else principal, person
            )
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
            exclude_committed_demand_id=demand.id if replace else None,
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
                                demand.id if replace else None,
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


def plan_lines(
    principal: Principal,
    session: Session,
    demand: CapacityDemand,
    body: PlanReplyInput | ChangeProposalInput,
    params: dict[str, JsonValue],
    *,
    borrow: bool,
) -> list[ProposalLineInput | BorrowLineInput]:
    if borrow:
        departments = params.get("departments")
        if (
            body.lender_department_id is None
            or body.borrow_headcount is None
            or body.borrow_headcount > demand.headcount
            or not isinstance(departments, list)
            or not any(
                isinstance(entry, dict)
                and entry.get("department_id") == str(body.lender_department_id)
                for entry in departments
            )
        ):
            raise ProposalConflict(
                "请从保存的借调方案选择借出部门和人数，人数不能超过需求"
            )
        return [
            BorrowLineInput(
                lender_department_id=body.lender_department_id,
                allocation_percent=demand.allocation_percent,
                start_date=demand.window_start,
                end_date=demand.window_end,
            )
            for _ in range(body.borrow_headcount)
        ]
    if body.lender_department_id is not None or body.borrow_headcount is not None:
        raise ProposalConflict("只有借调方案可以指定借出部门和人数")
    raw = params.get("assignees")
    if (
        not isinstance(raw, list)
        or not raw
        or not all(isinstance(item, dict) for item in raw)
    ):
        raise ProposalConflict("方案尚无可安排的人员，请重新规划")
    lines = [
        ProposalLineInput.model_validate({**item, "outside_basis": body.outside_basis})
        for item in raw
        if isinstance(item, dict)
    ]
    check_lines(
        principal, session, demand, lines, replace=isinstance(body, ChangeProposalInput)
    )
    return list(lines)


def create(
    principal: Principal,
    session: Session,
    demand: CapacityDemand,
    option_id: UUID,
    lines: list[ProposalLineInput | BorrowLineInput],
    *,
    change: ChangeProposalInput | None = None,
    via: Via = "web",
) -> CapacityProposal:
    assert principal.user_id is not None
    previous = None
    if change is not None:
        previous = latest(
            session, demand.org_id, demand.id, status=ProposalStatus.COMMITTED
        )
        if (
            previous is None
            or demand.status != DemandStatus.ACCEPTED
            or demand.revision != change.revision
            or latest(
                session,
                demand.org_id,
                demand.id,
                lock=True,
                status=ProposalStatus.PENDING,
            )
            is not None
        ):
            raise ProposalConflict("需求状态或版本已变化，请刷新后重新发起变更")
        if principal.user_id == demand.requester_user_id:
            raise ProposalSelfApprovalForbidden()
        validate_department_text(
            principal, session, demand.target_department_id, change.reason
        )
        if change.lender_department_id is not None:
            validate_department_text(
                principal, session, change.lender_department_id, change.reason
            )
        validate_load_words(principal, session, demand, change.reason)
        if (
            demand.committed_end_date
            and max(line.end_date for line in lines) > demand.committed_end_date
        ):
            raise ProposalConflict("推迟完成日期请走延期审批")
        demand.revision += 1
        session.add(demand)
        notify_parties(principal, session, demand, "有新的安排变更等待确认")
    row = CapacityProposal(
        org_id=principal.org_id,
        demand_id=demand.id,
        source_option_id=option_id,
        kind="change" if previous else "initial",
        replaces_proposal_id=previous.id if previous else None,
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
            reason=change.reason if change is not None else None,
            decision="approved",
            user_id=principal.user_id,
            via=via,
        )
    )
    session.flush()
    if isinstance(lines[0], BorrowLineInput):
        for user_id in responder_user_ids(
            session, principal.org_id, lines[0].lender_department_id
        ):
            add_item(
                session,
                InboxItem(
                    org_id=principal.org_id,
                    recipient_user_id=user_id,
                    kind=InboxKind.CAPACITY,
                    message="借调请求",
                ),
            )
    return row


def approvals_for(session: Session, row: CapacityProposal) -> list[CapacityApproval]:
    return list(
        session.exec(
            select(CapacityApproval)
            .where(
                CapacityApproval.org_id == row.org_id,
                CapacityApproval.proposal_id == row.id,
            )
            .order_by(col(CapacityApproval.created_at), col(CapacityApproval.id))
        ).all()
    )


def _grouped_lines(
    principal: Principal,
    session: Session,
    lines: list[CapacityProposalLine],
    *,
    detailed: bool,
) -> tuple[dict[UUID, list[ProposalSegment]], list[ProposalLinePublic] | None]:
    people = {}
    if detailed:
        people = {
            p.id: p
            for p in session.exec(
                select(Employee).where(
                    employee_scope_clause(principal),
                    col(Employee.id).in_(
                        {
                            line.employee_id
                            for line in lines
                            if line.lender_department_id is None
                            and line.employee_id is not None
                        }
                    ),
                )
            ).all()
        }
    groups: dict[UUID, list[ProposalSegment]] = {}
    details = []
    for line in sorted(
        lines, key=lambda line: (line.start_date, line.created_at, line.id)
    ):
        borrowed = line.lender_department_id is not None
        key = line.employee_id if line.employee_id is not None else line.id
        groups.setdefault(key, []).append(ProposalSegment.model_validate(line))
        if not borrowed and line.employee_id is not None and line.employee_id in people:
            details.append(
                ProposalLinePublic(
                    id=line.id,
                    employee_id=line.employee_id,
                    employee_name=people[line.employee_id].display_name,
                    start_date=line.start_date,
                    end_date=line.end_date,
                    allocation_percent=line.allocation_percent,
                    outside_basis=line.outside_basis,
                )
            )
    return groups, details if detailed else None


def arrangement(
    principal: Principal,
    session: Session,
    lines: list[CapacityProposalLine],
    *,
    detailed: bool,
) -> CommitmentArrangement:
    groups, details = _grouped_lines(principal, session, lines, detailed=detailed)
    return CommitmentArrangement(
        participants=[
            ProposalParticipant(number=index, segments=segments)
            for index, segments in enumerate(groups.values(), start=1)
        ],
        lines=details,
    )


def _aligned_participants(
    prev_groups: dict[UUID, list[ProposalSegment]],
    curr_groups: dict[UUID, list[ProposalSegment]],
) -> tuple[list[ParticipantPublic], list[ParticipantPublic]]:
    prev_numbers = {key: index for index, key in enumerate(prev_groups, start=1)}
    removed = [key for key in prev_groups if key not in curr_groups]
    added = [key for key in curr_groups if key not in prev_groups]
    replacements = list(zip(removed, added, strict=False))
    replaced_prev = {old_key for old_key, _ in replacements}
    replaced_curr = {
        new_key: prev_numbers[old_key] for old_key, new_key in replacements
    }
    next_number = len(prev_groups) + 1
    prev_participants: list[ParticipantPublic] = [
        ChangeProposalParticipant(
            number=prev_numbers[key],
            segments=segments,
            status=(
                "replaced"
                if key in replaced_prev
                else "removed"
                if key not in curr_groups
                else "unchanged"
            ),
        )
        for key, segments in prev_groups.items()
    ]
    curr_participants: list[ParticipantPublic] = []
    for key, segments in curr_groups.items():
        status: ParticipantChangeStatus
        if key in prev_numbers:
            number, status = prev_numbers[key], "unchanged"
        elif key in replaced_curr:
            number, status = replaced_curr[key], "replaced"
        else:
            number, status = next_number, "added"
            next_number += 1
        curr_participants.append(
            ChangeProposalParticipant(number=number, segments=segments, status=status)
        )
    curr_participants.sort(key=lambda item: item.number)
    return prev_participants, curr_participants


def public(
    principal: Principal,
    session: Session,
    demand: CapacityDemand,
    *,
    include_previous: bool = False,
) -> ProposalDetail | ProposalSummary | None:
    row = latest(session, principal.org_id, demand.id)
    if row is None or reply_superseded(demand, row):
        return None
    lines = lines_for(session, row)
    approvals = approvals_for(session, row)
    borrowed = any(line.lender_department_id is not None for line in lines)
    lender_count = sum(
        a.party == "lender_manager" and a.decision == "approved" for a in approvals
    )
    progress = None
    if borrowed:
        if row.status == ProposalStatus.FAILED:
            progress = "借调未达成"
        elif row.status == ProposalStatus.PENDING:
            progress = (
                f"原部门已同意 {lender_count}/{len(lines)}"
                if lender_count < len(lines)
                else "原部门已安排人员，等待本人同意"
                if sum(
                    a.party == "employee" and a.decision == "approved"
                    for a in approvals
                )
                < len(lines)
                else "借调已获同意，等待需求方接受"
            )
        else:
            progress = (
                "借调已生效" if row.status == ProposalStatus.COMMITTED else "借调已撤回"
            )
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
    previous = None
    if (
        include_previous
        and row.replaces_proposal_id is not None
        and row.status == ProposalStatus.PENDING
    ):
        prev_lines = [
            line.model_copy(update={"start_date": a.start_date, "end_date": a.due_date})
            for a, line in active_segments(
                session, row.org_id, row.demand_id, row.replaces_proposal_id
            )
        ]
        prev_groups, prev_details = _grouped_lines(
            principal, session, prev_lines, detailed=detailed
        )
        curr_groups, curr_details = _grouped_lines(
            principal, session, lines, detailed=detailed
        )
        prev_participants, curr_participants = _aligned_participants(
            prev_groups, curr_groups
        )
        previous = CommitmentArrangement(
            participants=prev_participants, lines=prev_details
        )
        current = CommitmentArrangement(
            participants=curr_participants, lines=curr_details
        )
    else:
        current = arrangement(principal, session, lines, detailed=detailed)
    summary = ProposalSummary(
        id=row.id,
        status=row.status,
        fail_reason=None if borrowed else row.fail_reason,
        revision=row.revision,
        hold_expires_at=row.hold_expires_at,
        committed_at=row.committed_at,
        kind="change" if row.kind == "change" else "initial",
        previous=previous,
        headcount=len(current.participants),
        borrow_progress=progress,
        participants=current.participants
        if not borrowed or row.kind == "change"
        else [],
        allocation_percents=sorted({line.allocation_percent for line in lines}),
        start_date=min(line.start_date for line in lines),
        end_date=max(line.end_date for line in lines),
        conditions=(
            next(
                (a.reason or "" for a in approvals if a.party == "resource_manager"), ""
            )
            if row.kind == "change"
            else (reply.text if reply else "")
        ),
        approvals=[
            ProposalApprovalPublic(
                party=a.party,
                decision=a.decision,
                created_at=a.created_at,
                reason=a.reason if detailed or a.user_id == principal.user_id else None,
            )
            for a in approvals
            if a.party not in ("employee", "lender_manager")
        ],
    )
    if not detailed:
        return summary
    return ProposalDetail(**summary.model_dump(), lines=current.lines or [])


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
    if reason == "borrow_declined":
        principal = Principal.system_worker(row.org_id)
    row.status, row.fail_reason = ProposalStatus.FAILED, reason
    row.revision += 1
    if row.kind != "change":
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


def check_requester_decision(
    principal: Principal,
    row: CapacityProposal,
    approvals: list[CapacityApproval],
    lines: list[CapacityProposalLine],
) -> None:
    if any(a.party == "requester" for a in approvals):
        raise decided(row)
    if any(
        a.user_id == principal.user_id and a.party in ("lender_manager", "employee")
        for a in approvals
    ) or any(
        line.lender_department_id is not None
        and line.employee_id is not None
        and line.employee_id == principal.employee_id
        for line in lines
    ):
        raise ProposalSelfApprovalForbidden()


def accept(
    principal: Principal,
    session: Session,
    demand: CapacityDemand,
    row: CapacityProposal,
    *,
    via: Via,
    requester_accepts: bool = True,
) -> None:
    failure_actor = (
        principal if requester_accepts else Principal.system_worker(row.org_id)
    )
    if expire_locked(failure_actor, session, demand, row, via=via):
        raise ProposalFailed("方案已过期，请对方重新安排", code="proposal_expired")
    lines = lines_for(session, row)
    lender_department_id = lines[0].lender_department_id
    approvals = approvals_for(session, row)
    requester = next((a for a in approvals if a.party == "requester"), None)
    if requester_accepts:
        check_requester_decision(principal, row, approvals, lines)
        assert principal.user_id is not None
        requester = CapacityApproval(
            org_id=principal.org_id,
            proposal_id=row.id,
            party="requester",
            decision="approved",
            user_id=principal.user_id,
            via=via,
        )
    employee_approvals = {
        a.line_id: a.user_id
        for a in approvals
        if a.party == "employee" and a.decision == "approved"
    }
    if lender_department_id is not None and (
        requester is None or set(employee_approvals) != {line.id for line in lines}
    ):
        if requester_accepts:
            session.add(requester)
            row.revision += 1
            session.add(row)
            session.flush()
        return
    assert requester is not None
    try:
        proposer = current_principal(session, principal.org_id, row.proposed_by_user_id)
        accepter = current_principal(session, principal.org_id, requester.user_id)
        if not represents_department(proposer, demand.target_department_id):
            raise ProposalConflict("提议人已没有目标部门的审批权限")
        if not (
            accepter.user_id == demand.requester_user_id
            or demand.requesting_department_id in accepter.managed_department_ids
        ):
            raise ProposalConflict("接受人已没有需求方审批权限")
        if demand.revision != row.demand_revision or demand.status != (
            DemandStatus.ACCEPTED if row.kind == "change" else DemandStatus.REPLIED
        ):
            raise ProposalConflict("需求版本已变化，请重新规划")
        if any(line.employee_id is None for line in lines):
            raise ProposalConflict("借调尚未完成审批")
        inputs = [ProposalLineInput.model_validate(line) for line in lines]
        if lender_department_id is None:
            check_lines(
                proposer,
                session,
                demand,
                inputs,
                exclude=row.id,
                replace=row.kind == "change",
                conflict_viewer=accepter,
            )
        else:
            representatives = responder_user_ids(
                session, row.org_id, lender_department_id
            )
            selected = {
                a.line_id: a.user_id
                for a in approvals
                if a.party == "lender_manager" and a.decision == "approved"
            }
            if (
                set(selected) != {line.id for line in lines}
                or not set(selected.values()) <= representatives
            ):
                raise ProposalConflict("原部门审批权限已变化")
            if any(
                current_principal(
                    session, row.org_id, employee_approvals[line.id]
                ).employee_id
                != line.employee_id
                for line in lines
            ):
                raise ProposalConflict("借调员工的账号已变化")
            lenders = {
                line_input.employee_id: current_principal(
                    session, row.org_id, selected[line.id]
                )
                for line, line_input in zip(lines, inputs, strict=True)
            }
            check_lines(
                next(iter(lenders.values())),
                session,
                demand,
                inputs,
                exclude=row.id,
                replace=row.kind == "change",
                lender_department_id=lender_department_id,
                lenders=lenders,
            )
    except ProposalConflict as error:
        if expire_locked(failure_actor, session, demand, row, via=via):
            raise ProposalFailed(
                "方案已过期，请对方重新安排", code="proposal_expired"
            ) from error
        fail(
            failure_actor,
            session,
            demand,
            row,
            "recheck_failed",
            "借调未达成"
            if lender_department_id is not None
            else "方案复核未通过，需要重新规划",
            via=via,
        )
        if lender_department_id is not None:
            raise ProposalFailed(
                "借调未达成", code="proposal_recheck_failed"
            ) from error
        # The accepter only receives department-level failure details.
        raise ProposalFailed(
            "方案复核未通过，请对方重新安排",
            code="proposal_recheck_failed",
            reason=error.detail,
        ) from error
    if expire_locked(failure_actor, session, demand, row, via=via):
        raise ProposalFailed("方案已过期，请对方重新安排", code="proposal_expired")
    if requester_accepts:
        session.add(requester)
    acceptance_via = cast(Via, requester.via)
    old_segments = (
        active_segments(session, row.org_id, demand.id, row.replaces_proposal_id)
        if row.replaces_proposal_id is not None
        else []
    )
    unchanged: dict[tuple[UUID, date | None, date, int], list[CapacityAssignment]] = (
        defaultdict(list)
    )
    for assignment, _ in old_segments:
        unchanged[
            (
                assignment.employee_id,
                assignment.start_date,
                assignment.due_date,
                assignment.allocation_percent,
            )
        ].append(assignment)
    previous_end = demand.committed_end_date
    added = []
    for line in lines:
        assert line.employee_id is not None
        retained = unchanged.get(
            (line.employee_id, line.start_date, line.end_date, line.allocation_percent)
        )
        if retained:
            kept = retained.pop()
            kept.proposal_line_id = line.id
            assert_assignment_line(session, kept)
            session.add(kept)
        else:
            added.append(line)
    for replaced in unchanged.values():
        for assignment in replaced:
            assignment.status, assignment.closed_at = (
                AssignmentStatus.CANCELLED,
                utc_now(),
            )
            session.add(assignment)
    if old_segments:
        session.flush()
    for line in added:
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
            accepter,
            session,
            "capacity.assignment_created",
            "capacity_assignment",
            assignment.id,
            acceptance_via,
            None,
            {
                "proposal_id": str(row.id),
                "proposal_line_id": str(line.id),
                "outside_basis": line.outside_basis,
            },
        )
    change = row.kind == "change"
    if change:
        demand.committed_start_date = min(line.start_date for line in lines)
        demand.committed_end_date = max(line.end_date for line in lines)
    row.status, row.committed_at = ProposalStatus.COMMITTED, utc_now()
    row.revision += 1
    demand.status = DemandStatus.ACCEPTED
    demand.revision += 1
    session.add_all([row, demand])
    event_kinds = (
        (DemandEventKind.COMMITMENT_CHANGED,)
        if change
        else (
            DemandEventKind.ACCEPTED,
            DemandEventKind.COMMITTED,
        )
    )
    for kind in event_kinds:
        session.add(
            CapacityDemandEvent(
                org_id=principal.org_id,
                demand_id=demand.id,
                actor_user_id=requester.user_id,
                kind=kind,
                text="安排变更已生效" if change else "",
                start_date=demand.committed_start_date if change else None,
                end_date=demand.committed_end_date if change else None,
                basis={
                    "previous_end_date": previous_end.isoformat(),
                    "revision": demand.revision,
                }
                if change and previous_end
                else {},
                via=acceptance_via,
            )
        )
    changed_people = {
        line.employee_id for line in added if line.employee_id is not None
    } | {
        assignment.employee_id
        for replaced in unchanged.values()
        for assignment in replaced
    }
    for employee_id in sorted(changed_people):
        notify_employee(principal, session, employee_id)
    notify_parties(
        principal,
        session,
        demand,
        "安排变更已生效" if change else "方案已接受，安排已生效",
    )
    audit(
        accepter,
        session,
        "capacity.commitment_changed" if change else "capacity.demand_accepted",
        "capacity_demand",
        demand.id,
        acceptance_via,
        None,
        {
            "proposal_id": str(row.id),
            **(
                {"replaces_proposal_id": str(row.replaces_proposal_id)}
                if change
                else {}
            ),
        },
    )
    session.flush()


def borrow_requests(
    principal: Principal, session: Session, *, mine: bool
) -> list[BorrowRequest]:
    require_enabled(principal, session)
    if mine and principal.employee_id is None:
        return []
    rows = session.exec(
        select(CapacityProposalLine, CapacityProposal, CapacityDemand)
        .join(
            CapacityProposal,
            (col(CapacityProposal.id) == CapacityProposalLine.proposal_id)
            & (col(CapacityProposal.org_id) == CapacityProposalLine.org_id),
        )
        .join(
            CapacityDemand,
            (col(CapacityDemand.id) == CapacityProposal.demand_id)
            & (col(CapacityDemand.org_id) == CapacityProposal.org_id),
        )
        .where(
            CapacityProposalLine.org_id == principal.org_id,
            col(CapacityProposalLine.lender_department_id).is_not(None),
            col(CapacityProposal.status).in_(
                (ProposalStatus.PENDING, ProposalStatus.FAILED)
            ),
            *(
                [CapacityProposalLine.employee_id == principal.employee_id]
                if mine
                else []
            ),
        )
        .order_by(col(CapacityProposal.created_at).desc(), col(CapacityProposalLine.id))
    ).all()
    result: list[BorrowRequest] = []
    representatives: dict[UUID, set[UUID]] = {}
    candidates: dict[UUID, list[BorrowCandidate]] = {}
    for line, row, demand in rows:
        lender = line.lender_department_id
        assert lender is not None
        if not mine:
            if lender not in representatives:
                representatives[lender] = responder_user_ids(
                    session, row.org_id, lender
                )
            if principal.user_id not in representatives[lender]:
                continue
        own = None
        if mine:
            own = next(
                (
                    a
                    for a in approvals_for(session, row)
                    if a.party == "employee"
                    and a.line_id == line.id
                    and a.user_id == principal.user_id
                ),
                None,
            )
        if row.status == ProposalStatus.FAILED or row.hold_expires_at <= utc_now():
            status = "failed"
        elif mine:
            status = "waiting_commitment" if own is not None else "pending_consent"
        else:
            status = "pending_selection" if line.employee_id is None else "selected"
        if status == "pending_selection" and demand.id not in candidates:
            candidates[demand.id] = [
                BorrowCandidate(
                    employee_id=person.employee_id, display_name=person.display_name
                )
                for person in qualified_people(
                    principal, session, demand, lender
                ).values()
                if person.employee_id != principal.employee_id
            ]
        result.append(
            BorrowRequest(
                id=line.id,
                description=demand.description,
                capability_name=demand.capability_name,
                borrower_department=department(
                    session, row.org_id, demand.target_department_id
                ).name,
                allocation_percent=line.allocation_percent,
                start_date=line.start_date,
                end_date=line.end_date,
                status=status,
                reason=own.reason if mine and own else None,
                candidates=candidates[demand.id]
                if status == "pending_selection"
                else [],
            )
        )
    return result


def decide_borrow(
    principal: Principal, session: Session, line_id: UUID, body: BorrowDecisionInput
) -> None:
    require_enabled(principal, session)
    line, row = session.exec(
        select(CapacityProposalLine, CapacityProposal)
        .join(
            CapacityProposal,
            (col(CapacityProposal.id) == CapacityProposalLine.proposal_id)
            & (col(CapacityProposal.org_id) == CapacityProposalLine.org_id),
        )
        .where(
            CapacityProposalLine.org_id == principal.org_id,
            CapacityProposalLine.id == line_id,
        )
    ).one_or_none() or (None, None)
    if (
        line is None
        or row is None
        or line.lender_department_id is None
        or principal.user_id is None
    ):
        raise CapacityNotFound()
    demand = session.exec(
        select(CapacityDemand)
        .where(CapacityDemand.org_id == row.org_id, CapacityDemand.id == row.demand_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    session.refresh(row, with_for_update=True)
    session.refresh(line)
    manager = body.action in ("select", "reject")
    if manager:
        if principal.user_id not in responder_user_ids(
            session, row.org_id, line.lender_department_id
        ):
            raise CapacityNotFound()
    elif line.employee_id is None or line.employee_id != principal.employee_id:
        raise CapacityNotFound()
    if row.status != ProposalStatus.PENDING:
        raise decided(row)
    failure_actor = principal if manager else Principal.system_worker(row.org_id)
    if expire_locked(failure_actor, session, demand, row):
        raise ProposalFailed("借调未达成", code="proposal_expired")
    party = "lender_manager" if manager else "employee"
    approvals = approvals_for(session, row)
    if any(a.party == party and a.line_id == line.id for a in approvals):
        raise decided(row)
    lines = lines_for(session, row)
    if (
        principal.user_id in (row.proposed_by_user_id, demand.requester_user_id)
        or any(a.user_id == principal.user_id and a.party != party for a in approvals)
        or (
            manager
            and any(
                other.employee_id is not None
                and other.employee_id == principal.employee_id
                for other in lines
            )
        )
    ):
        raise ProposalSelfApprovalForbidden()
    if body.action == "select":
        if body.employee_id is None:
            raise ProposalConflict("请选择借调员工")
        if any(other.employee_id == body.employee_id for other in lines):
            raise ProposalConflict("这名员工已在本次借调中")
        member = session.exec(
            select(OrgMembership)
            .join(User, col(OrgMembership.user_id) == User.id)
            .join(
                Employee,
                (col(Employee.org_id) == OrgMembership.org_id)
                & (col(Employee.id) == OrgMembership.employee_id),
            )
            .where(
                employee_scope_clause(principal),
                OrgMembership.org_id == row.org_id,
                OrgMembership.employee_id == body.employee_id,
                User.is_active,
            )
        ).one_or_none()
        if member is None:
            raise ProposalConflict("借调员工需要可登录的账号")
        if member.user_id in (
            principal.user_id,
            row.proposed_by_user_id,
            demand.requester_user_id,
            *(a.user_id for a in approvals if a.party != "employee"),
        ):
            raise ProposalSelfApprovalForbidden()
        check_lines(
            principal,
            session,
            demand,
            [
                ProposalLineInput(
                    employee_id=body.employee_id,
                    allocation_percent=line.allocation_percent,
                    start_date=line.start_date,
                    end_date=line.end_date,
                )
            ],
            lender_department_id=line.lender_department_id,
            replace=row.kind == "change",
        )
        if expire_locked(principal, session, demand, row):
            raise ProposalFailed("借调未达成", code="proposal_expired")
        line.employee_id = body.employee_id
        session.add(line)
        add_item(
            session,
            InboxItem(
                org_id=row.org_id,
                recipient_employee_id=line.employee_id,
                kind=InboxKind.CAPACITY,
                message="借调请求待你确认",
            ),
        )
    if body.action == "reject" and body.reason is None:
        raise ProposalConflict("请填写拒绝理由")
    declined = body.action in ("reject", "decline")
    session.add(
        CapacityApproval(
            org_id=row.org_id,
            proposal_id=row.id,
            line_id=line.id,
            user_id=principal.user_id,
            party=party,
            via="web",
            decision="rejected" if declined else "approved",
            reason=(body.reason or "未填写理由") if declined else None,
        )
    )
    if declined:
        fail(
            principal,
            session,
            demand,
            row,
            "rejected" if manager else "borrow_declined",
            "借调未达成",
        )
    else:
        row.revision += 1
        session.add(row)
        session.flush()
        if not manager:
            accept(principal, session, demand, row, via="web", requester_accepts=False)


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
    if row.kind == "change" and via != "web":
        raise ProposalConflict("请在网页发起或确认变更")
    if row.kind == "change" and action == "withdraw":
        raise ProposalConflict("已生效的需求不能撤回，可拒绝本次变更")
    if via == "mcp" and action == "accept":
        raise CapacityForbidden()
    if principal.user_id == row.proposed_by_user_id and action in (
        "accept",
        "follow_up",
    ):
        raise ProposalSelfApprovalForbidden()
    expected = (
        row.demand_revision
        if row.kind == "change"
        and action == "accept"
        and revision == row.demand_revision
        else demand.revision
    )
    check_pending(row, revision, expected)
    if action == "accept":
        accept(principal, session, demand, row, via=via)
    elif action == "follow_up":
        check_requester_decision(
            principal, row, approvals_for(session, row), lines_for(session, row)
        )
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
            "安排变更被拒绝"
            if row.kind == "change"
            else f"方案被拒，需要重新规划：{text}",
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
    if row.kind != "change":
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
