from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app.domains.base import utc_now
from app.domains.capacity.models import (
    AssignmentStatus,
    CapacityAssignment,
    CapacityProposal,
    CapacityProposalLine,
    CapacityUnavailability,
    CapacityWeek,
    ProposalStatus,
)
from app.domains.org.timezone import read_timezone

FRESH_DAYS = 28


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def this_week(session: Session, org_id: UUID) -> date:
    return week_start(
        utc_now().astimezone(ZoneInfo(read_timezone(session, org_id).timezone)).date()
    )


class HoldLoad(BaseModel):
    allocation_percent: int
    expires_at: datetime


class WeekLoad(BaseModel):
    week_start: date
    capacity_percent: int
    other_percent: int | None
    platform_percent: int
    hold_percent: int = 0
    holds: list[HoldLoad] = Field(default_factory=list)
    status: Literal["unconfirmed", "confirmed", "stale"]
    confirmed_at: datetime | None
    legacy_start: bool


def loads(
    session: Session,
    org_id: UUID,
    employee_ids: list[UUID],
    from_week: date,
    weeks: int,
    *,
    exclude_assignment_id: UUID | None = None,
    exclude_proposal_id: UUID | None = None,
    exclude_demand_id: UUID | None = None,
    exclude_committed_demand_id: UUID | None = None,
) -> dict[UUID, list[WeekLoad]]:
    end = from_week + timedelta(weeks=weeks)
    records = {
        (r.employee_id, r.week_start): r
        for r in session.exec(
            select(CapacityWeek).where(
                CapacityWeek.org_id == org_id,
                col(CapacityWeek.employee_id).in_(employee_ids),
                CapacityWeek.week_start >= from_week,
                CapacityWeek.week_start < end,
            )
        ).all()
    }
    assignments = session.exec(
        select(CapacityAssignment).where(
            CapacityAssignment.org_id == org_id,
            col(CapacityAssignment.employee_id).in_(employee_ids),
            CapacityAssignment.status == AssignmentStatus.ACTIVE,
            CapacityAssignment.due_date >= from_week,
        )
    ).all()
    holds = session.exec(
        select(CapacityProposalLine, CapacityProposal)
        .join(
            CapacityProposal,
            (col(CapacityProposal.org_id) == CapacityProposalLine.org_id)
            & (col(CapacityProposal.id) == CapacityProposalLine.proposal_id),
        )
        .where(
            CapacityProposalLine.org_id == org_id,
            col(CapacityProposalLine.employee_id).in_(employee_ids),
            CapacityProposal.status == ProposalStatus.PENDING,
            CapacityProposal.hold_expires_at > utc_now(),
            CapacityProposalLine.end_date >= from_week,
            CapacityProposalLine.start_date < end,
            *(
                [CapacityProposal.id != exclude_proposal_id]
                if exclude_proposal_id
                else []
            ),
            *(
                [CapacityProposal.demand_id != exclude_demand_id]
                if exclude_demand_id
                else []
            ),
        )
    ).all()
    unavailable = session.exec(
        select(CapacityUnavailability).where(
            CapacityUnavailability.org_id == org_id,
            col(CapacityUnavailability.employee_id).in_(employee_ids),
            CapacityUnavailability.end_date >= from_week,
            CapacityUnavailability.start_date < end,
        )
    ).all()
    cutoff = utc_now() - timedelta(days=FRESH_DAYS)
    result: dict[UUID, list[WeekLoad]] = {}
    for employee_id in employee_ids:
        result[employee_id] = []
        for offset in range(weeks):
            start = from_week + timedelta(weeks=offset)
            record = records.get((employee_id, start))
            overlapping = [
                a
                for a in assignments
                if a.employee_id == employee_id
                and (a.start_date or week_start(a.created_at.date()))
                <= start + timedelta(days=6)
                and a.due_date >= start
            ]
            days = sum(
                not any(
                    u.employee_id == employee_id
                    and u.start_date <= start + timedelta(days=d) <= u.end_date
                    for u in unavailable
                )
                for d in range(5)
            )
            amounts: dict[UUID, int] = defaultdict(int)
            proposals: dict[UUID, CapacityProposal] = {}
            details: list[HoldLoad] = []
            for line, proposal in holds:
                if (
                    line.employee_id == employee_id
                    and line.start_date <= start + timedelta(days=6)
                    and line.end_date >= start
                ):
                    if proposal.kind == "change":
                        amounts[proposal.id] += line.allocation_percent
                        proposals[proposal.id] = proposal
                    else:
                        details.append(
                            HoldLoad(
                                allocation_percent=line.allocation_percent,
                                expires_at=proposal.hold_expires_at,
                            )
                        )
            for proposal_id, amount in amounts.items():
                proposal = proposals[proposal_id]
                amount -= sum(
                    a.allocation_percent
                    for a in overlapping
                    if a.demand_id == proposal.demand_id
                    and a.proposal_line_id is not None
                )
                if amount > 0:
                    details.append(
                        HoldLoad(
                            allocation_percent=amount,
                            expires_at=proposal.hold_expires_at,
                        )
                    )
            overlapping = [
                a
                for a in overlapping
                if a.id != exclude_assignment_id
                and not (
                    a.demand_id == exclude_committed_demand_id
                    and a.proposal_line_id is not None
                )
            ]
            held = sum(hold.allocation_percent for hold in details)
            result[employee_id].append(
                WeekLoad(
                    week_start=start,
                    capacity_percent=100 * days // 5,
                    other_percent=record.other_percent if record else None,
                    platform_percent=sum(a.allocation_percent for a in overlapping)
                    + held,
                    hold_percent=held,
                    holds=details,
                    status="unconfirmed"
                    if record is None
                    else "stale"
                    if record.confirmed_at < cutoff
                    else "confirmed",
                    confirmed_at=record.confirmed_at if record else None,
                    legacy_start=any(a.start_date is None for a in overlapping),
                )
            )
    return result


def shortfalls(
    week_loads: list[WeekLoad], allocation_percent: int
) -> tuple[list[date], list[tuple[date, int]]]:
    unconfirmed = [w.week_start for w in week_loads if w.status != "confirmed"]
    over = [
        (
            w.week_start,
            w.other_percent
            + w.platform_percent
            + allocation_percent
            - w.capacity_percent,
        )
        for w in week_loads
        if w.other_percent is not None
        and w.other_percent + w.platform_percent + allocation_percent
        > w.capacity_percent
    ]
    return unconfirmed, over
