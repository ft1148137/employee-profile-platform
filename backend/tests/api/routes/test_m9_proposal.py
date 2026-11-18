"""Proposal holds and atomic commitments on test-owned synthetic data."""

from collections.abc import Generator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, event
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select

from app.core.security import create_access_token
from app.domains.base import utc_now
from app.domains.capacity import proposal
from app.domains.capacity.calendar import loads
from app.domains.capacity.models import (
    CapacityApproval,
    CapacityAssignment,
    CapacityDemandEvent,
    CapacityExtensionRequest,
    CapacityProposal,
    CapacityProposalLine,
    CapacityWeek,
    DemandEventKind,
)
from app.domains.org.models import OrgMembership, RoleGrant
from app.domains.planning.models import PlanningOption
from app.models import User
from tests.api.routes.test_m7_planning_validator import planning_setup as planning_setup


@pytest.fixture
def case(planning_setup: tuple, db: Session) -> Generator[tuple]:
    # The baseline fixture creates a fresh tenant; C1 additions are cleaned here.
    url, people, auth, demand = planning_setup
    auth["a"] = {
        "Authorization": f"Bearer {create_access_token(demand.requester_user_id, expires_delta=timedelta(hours=1))}"
    }
    yield url, people, auth, demand
    db.rollback()
    for model in (
        CapacityExtensionRequest,
        CapacityAssignment,
        CapacityApproval,
        CapacityProposalLine,
        CapacityProposal,
        CapacityDemandEvent,
    ):
        db.exec(delete(model).where(model.org_id == demand.org_id))
    db.commit()


def segments(
    case: tuple, *, split: bool = False, percent: int = 50, people: int = 1
) -> list[dict]:
    _, employees, _, demand = case
    result = []
    for code in ("E04", "E05")[:people]:
        periods = [(percent, demand.window_start, demand.window_end)]
        if split:
            middle = demand.window_start + timedelta(days=7)
            periods = [
                (30, demand.window_start, middle - timedelta(days=1)),
                (50, middle, demand.window_end),
            ]
        result.extend(
            {
                "employee_id": str(employees[code].id),
                "allocation_percent": p,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
            for p, start, end in periods
        )
    return result


def plan(client: TestClient, case: tuple, lines: list[dict] | None = None) -> dict:
    url, _, auth, demand = case
    created = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert created.status_code == 200, created.text
    result = client.post(
        f"{url}/planning/cases/{created.json()['id']}/options",
        headers=auth["b"],
        json={"assignees": lines or segments(case)},
    )
    assert result.status_code == 200, result.text
    return result.json()


def propose(
    client: TestClient, case: tuple, option: dict | None = None, **extra: Any
) -> Response:
    url, _, auth, demand = case
    option = option or plan(client, case)
    return client.post(
        f"{url}/capacity/demands/{demand.id}/reply",
        headers=auth["b"],
        json={
            "option_id": option["id"],
            "outcome": "can_do",
            "text": "可以按方案承接",
            "revision": demand.revision,
            "outside_basis": True,
            **extra,
        },
    )


def respond(
    client: TestClient,
    case: tuple,
    revision: int,
    action: str = "accept",
    *,
    role: str = "a",
    **extra: Any,
) -> Response:
    url, _, auth, demand = case
    return client.post(
        f"{url}/capacity/demands/{demand.id}/respond",
        headers=auth[role],
        json={"action": action, "revision": revision, **extra},
    )


def rows(db: Session, model: Any, demand: Any) -> list[Any]:
    statement = select(model).where(model.org_id == demand.org_id)
    if hasattr(model, "demand_id"):
        statement = statement.where(model.demand_id == demand.id)
    return list(db.exec(statement.order_by(model.created_at, model.id)).all())


def proposal_row(db: Session, demand: Any) -> CapacityProposal:
    return db.exec(
        select(CapacityProposal)
        .where(
            CapacityProposal.org_id == demand.org_id,
            CapacityProposal.demand_id == demand.id,
        )
        .execution_options(populate_existing=True)
    ).one()


def counts(db: Session, demand: Any) -> tuple[int, ...]:
    db.expire_all()
    return tuple(
        len(rows(db, model, demand))
        for model in (CapacityAssignment, CapacityApproval, CapacityDemandEvent)
    )


def test_reply_with_option_creates_hold(
    case: tuple, client: TestClient, db: Session
) -> None:
    url, people, auth, demand = case
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    assert reply.json()["proposal"]["status"] == "pending"
    (approval,) = rows(db, CapacityApproval, demand)
    (event,) = [
        event
        for event in rows(db, CapacityDemandEvent, demand)
        if event.kind == DemandEventKind.REPLIED
    ]
    assert approval.party == "resource_manager" and approval.via == event.via == "web"
    load = loads(db, demand.org_id, [people["E04"].id], demand.window_start, 2)[
        people["E04"].id
    ][0]
    assert load.hold_percent == load.platform_percent == 50
    team = client.get(f"{url}/capacity/workloads", headers=auth["b"]).json()
    person = next(p for p in team["data"] if p["employee_id"] == str(people["E04"].id))
    assert person["weeks"][0]["hold_percent"] == 50
    a = client.get(f"{url}/capacity/demands/{demand.id}", headers=auth["a"])
    assert a.status_code == 200
    for secret in (
        str(people["E04"].id),
        people["E04"].display_name,
        "employee_id",
        '"lines"',
    ):
        assert secret not in a.text
    for role in ("employee", "foreign"):
        assert (
            client.get(
                f"{url}/capacity/demands/{demand.id}", headers=auth[role]
            ).status_code
            == 404
        )
    assert (
        client.get(f"{url}/capacity/assignments/me", headers=auth["employee"]).json()[
            "count"
        ]
        == 0
    )


def test_current_assignment_constraint_rejects_valid_segments(
    case: tuple, client: TestClient, db: Session
) -> None:
    # E40 replaces the old constraint reproduction with two actual assignments.
    _, people, _, demand = case
    reply = propose(client, case, plan(client, case, segments(case, split=True)))
    assert reply.status_code == 200, reply.text
    accepted = respond(client, case, reply.json()["revision"])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["proposal"]["status"] == "committed"
    assert accepted.json()["status"] == "accepted"
    assigned = rows(db, CapacityAssignment, demand)
    assert len(assigned) == 2 and len({a.proposal_line_id for a in assigned}) == 2
    weekly = loads(db, demand.org_id, [people["E04"].id], demand.window_start, 2)[
        people["E04"].id
    ]
    assert [w.platform_percent for w in weekly] == [30, 50]
    assert [w.hold_percent for w in weekly] == [0, 0]
    assert len(rows(db, CapacityApproval, demand)) == 2
    assert {e.kind for e in rows(db, CapacityDemandEvent, demand)} >= {
        "replied",
        "accepted",
        "committed",
    }
    before = counts(db, demand)
    repeated = respond(client, case, reply.json()["revision"])
    assert (
        repeated.status_code == 409
        and repeated.json()["detail"]["code"] == "proposal_decided"
    )
    assert counts(db, demand) == before
    url, _, auth, _ = case
    payload = {k: v for k, v in segments(case)[0].items() if k != "end_date"}
    payload.update(due_date=demand.window_end.isoformat(), outside_basis=True)
    manual = client.post(
        f"{url}/capacity/demands/{demand.id}/assignments",
        headers=auth["b"],
        json=payload,
    )
    assert manual.status_code == 409 and "发起变更" in manual.text
    payload["proposal_line_id"] = str(assigned[0].proposal_line_id)
    assert (
        client.post(
            f"{url}/capacity/demands/{demand.id}/assignments",
            headers=auth["b"],
            json=payload,
        ).status_code
        == 422
    )


@pytest.mark.parametrize("change", ["inactive", "capacity", "role"])
def test_recheck_failure_is_persisted(
    case: tuple, client: TestClient, db: Session, change: str
) -> None:
    _, people, _, demand = case
    reply = propose(client, case, plan(client, case, segments(case, people=2)))
    assert reply.status_code == 200, reply.text
    if change == "inactive":
        person = people["E04"]
        person.employment_status = "inactive"
        db.add(person)
    elif change == "capacity":
        week = db.exec(
            select(CapacityWeek).where(
                CapacityWeek.org_id == demand.org_id,
                CapacityWeek.employee_id == people["E04"].id,
                CapacityWeek.week_start == demand.window_start,
            )
        ).one()
        week.other_percent = 70
        db.add(week)
    else:
        member = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == demand.org_id,
                OrgMembership.employee_id == people["E05"].id,
            )
        ).one()
        db.exec(
            delete(RoleGrant).where(
                RoleGrant.org_id == demand.org_id, RoleGrant.membership_id == member.id
            )
        )
    db.commit()
    response = respond(client, case, reply.json()["revision"])
    assert response.status_code == 409, response.text
    assert not rows(db, CapacityAssignment, demand)
    assert proposal_row(db, demand).status == "failed"
    db.refresh(demand)
    assert demand.status == "submitted"
    assert (
        loads(db, demand.org_id, [people["E04"].id], demand.window_start, 2)[
            people["E04"].id
        ][0].hold_percent
        == 0
    )


@pytest.mark.parametrize("action", ["follow_up", "withdraw", "b_withdraw"])
def test_rejection_and_withdraw_are_idempotent(
    case: tuple, client: TestClient, db: Session, action: str
) -> None:
    url, _, auth, demand = case
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text

    def send() -> Response:
        if action == "b_withdraw":
            return client.post(
                f"{url}/capacity/proposals/{reply.json()['proposal']['id']}/withdraw",
                headers=auth["b"],
                json={"revision": reply.json()["proposal"]["revision"]},
            )
        return respond(
            client, case, reply.json()["revision"], action, text="请调整交付时间"
        )

    result = send()
    assert result.status_code == 200, result.text
    assert result.json()["status"] == (
        "withdrawn" if action == "withdraw" else "submitted"
    )
    before = counts(db, demand)
    assert send().json()["detail"]["code"] == "proposal_decided"
    assert counts(db, demand) == before
    if action == "follow_up":
        rejected = [
            a for a in rows(db, CapacityApproval, demand) if a.party == "requester"
        ]
        assert rejected[0].reason == "请调整交付时间"


@pytest.mark.parametrize(
    ("ending", "action"),
    [
        ("rejected", "accept"),
        ("expired", "accept"),
        ("withdrawn", "accept"),
        ("rejected", "follow_up"),
        ("rejected", "withdraw"),
    ],
)
def test_response_uses_new_plain_reply_after_proposal_ends(
    case: tuple, client: TestClient, db: Session, ending: str, action: str
) -> None:
    url, people, auth, demand = case
    path = f"{url}/capacity/demands/{demand.id}"
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    old_revision = reply.json()["revision"]
    if ending == "rejected":
        result = respond(client, case, old_revision, "follow_up", text="请调整方案")
        assert result.status_code == 200, result.text
    elif ending == "expired":
        row = proposal_row(db, demand)
        row.hold_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
        result = respond(client, case, old_revision)
        assert result.json()["detail"]["code"] == "proposal_expired"
    else:
        previous = reply.json()["proposal"]
        result = client.post(
            f"{url}/capacity/proposals/{previous['id']}/withdraw",
            headers=auth["b"],
            json={"revision": previous["revision"]},
        )
        assert result.status_code == 200, result.text

    submitted = client.get(path, headers=auth["a"]).json()
    assert submitted["status"] == "submitted"
    assert submitted["proposal"]["status"] in ("failed", "withdrawn")
    plain = client.post(
        f"{path}/reply",
        headers=auth["b"],
        json={
            "revision": submitted["revision"],
            "outcome": "can_do",
            "text": "可以按期承接",
            "start_date": demand.window_start.isoformat(),
            "end_date": demand.window_end.isoformat(),
        },
    )
    assert plain.status_code == 200, plain.text
    before = counts(db, demand)
    replay = respond(client, case, old_revision, action, text="合成重试")
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "proposal_decided"
    assert counts(db, demand) == before

    result = respond(client, case, plain.json()["revision"], action, text="合成回应")
    assert result.status_code == 200, result.text
    assert (
        result.json()["status"]
        == {
            "accept": "accepted",
            "follow_up": "submitted",
            "withdraw": "withdrawn",
        }[action]
    )
    assert result.json()["proposal"] is None
    assert counts(db, demand) == (before[0], before[1], before[2] + 1)
    assert proposal_row(db, demand).status == submitted["proposal"]["status"]
    if action == "accept":
        assigned = client.post(
            f"{path}/assignments",
            headers=auth["b"],
            json={
                "employee_id": str(people["E04"].id),
                "allocation_percent": 50,
                "start_date": demand.window_start.isoformat(),
                "due_date": demand.window_end.isoformat(),
                "outside_basis": True,
            },
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["proposal_line_id"] is None


@pytest.mark.parametrize("worker", [False, True])
def test_expired_hold_is_released_without_worker(
    case: tuple, client: TestClient, db: Session, worker: bool
) -> None:
    _, people, _, demand = case
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    row = proposal_row(db, demand)
    row.hold_expires_at = utc_now() - timedelta(seconds=1)
    db.add(row)
    db.commit()
    assert (
        loads(db, demand.org_id, [people["E04"].id], demand.window_start, 2)[
            people["E04"].id
        ][0].hold_percent
        == 0
    )
    if worker:
        from app.authz.principal import Principal

        assert proposal.expire(Principal.system_worker(demand.org_id), db) == 1
        db.commit()
        assert proposal.expire(Principal.system_worker(demand.org_id), db) == 0
    else:
        assert respond(client, case, reply.json()["revision"]).status_code == 409
    assert proposal_row(db, demand).fail_reason == "expired"
    assert not rows(db, CapacityAssignment, demand)


def test_unexpected_second_assignment_error_rolls_everything_back(
    case: tuple, client: TestClient, db: Session
) -> None:
    reply = propose(client, case, plan(client, case, segments(case, split=True)))
    assert reply.status_code == 200, reply.text
    seen = 0

    def inject(_mapper: Any, _connection: Any, _target: CapacityAssignment) -> None:
        nonlocal seen
        seen += 1
        if seen == 2:
            raise RuntimeError("synthetic second assignment failure")

    event.listen(CapacityAssignment, "before_insert", inject)
    try:
        with pytest.raises(RuntimeError, match="synthetic second"):
            respond(client, case, reply.json()["revision"])
    finally:
        event.remove(CapacityAssignment, "before_insert", inject)
    assert not rows(db, CapacityAssignment, case[3])
    assert proposal_row(db, case[3]).status == "pending"
    assert len(rows(db, CapacityApproval, case[3])) == 1


@pytest.mark.parametrize("action", ["accept", "follow_up"])
@pytest.mark.parametrize("role", ["b", "admin"])
def test_proposer_cannot_approve_as_requesting_manager(
    case: tuple, client: TestClient, db: Session, action: str, role: str
) -> None:
    _, people, auth, demand = case
    if role == "admin":
        user = db.exec(select(User).where(User.email == "admin@demo.example.com")).one()
        member = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == demand.org_id, OrgMembership.user_id == user.id
            )
        ).one()
        db.add(
            RoleGrant(
                org_id=demand.org_id,
                membership_id=member.id,
                role="dept_manager",
                department_id=demand.target_department_id,
            )
        )
        auth["b"] = auth["admin"]
    else:
        member = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == demand.org_id,
                OrgMembership.employee_id == people["E05"].id,
            )
        ).one()
    db.add(
        RoleGrant(
            org_id=demand.org_id,
            membership_id=member.id,
            role="dept_manager",
            department_id=demand.requesting_department_id,
        )
    )
    db.commit()
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    before = counts(db, demand)
    result = respond(
        client, case, reply.json()["revision"], action, role="b", text="调整"
    )
    assert result.status_code == 403 and "不能审批自己" in result.text
    assert counts(db, demand) == before


@pytest.mark.parametrize("mismatch", ["employee", "demand"])
def test_assignment_line_ownership_and_unique_index(
    case: tuple, client: TestClient, db: Session, mismatch: str
) -> None:
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    row = proposal_row(db, case[3])
    line = proposal.lines_for(db, row)[0]
    bad = CapacityAssignment(
        org_id=row.org_id,
        demand_id=row.demand_id,
        employee_id=case[1]["E05"].id,
        proposal_line_id=line.id,
        allocation_percent=50,
        start_date=line.start_date,
        due_date=line.end_date,
        assigned_by_user_id=row.proposed_by_user_id,
    )
    if mismatch == "demand":
        bad.employee_id = line.employee_id
        bad.demand_id = uuid4()
    with pytest.raises(proposal.ProposalOwnershipError):
        proposal.assert_assignment_line(db, bad)
    db.rollback()
    accepted = respond(client, case, reply.json()["revision"])
    assert accepted.status_code == 200, accepted.text
    original = rows(db, CapacityAssignment, case[3])[0]
    duplicate = CapacityAssignment(**original.model_dump(exclude={"id", "created_at"}))
    with pytest.raises(IntegrityError, match="uq_capacity_assignment_proposal_line"):
        with db.begin_nested():
            db.add(duplicate)
            db.flush()


@pytest.mark.parametrize(
    "invalid", ["not_now", "infeasible", "insufficient_data", "stale", "malformed"]
)
def test_invalid_or_stale_option_never_holds(
    case: tuple, client: TestClient, db: Session, invalid: str
) -> None:
    saved = plan(client, case)
    if invalid in ("infeasible", "insufficient_data"):
        row = db.get(PlanningOption, UUID(saved["id"]))
        row.verdict = invalid
        db.add(row)
        db.commit()
    elif invalid == "malformed":
        row = db.get(PlanningOption, UUID(saved["id"]))
        row.params = {"assignees": [*segments(case), "invalid-line"]}
        db.add(row)
        db.commit()
    elif invalid == "stale":
        row = db.exec(
            select(CapacityWeek).where(
                CapacityWeek.org_id == case[3].org_id,
                CapacityWeek.employee_id == case[1]["E04"].id,
                CapacityWeek.week_start == case[3].window_start,
            )
        ).one()
        row.other_percent += 1
        db.add(row)
        db.commit()
    result = propose(
        client, case, saved, **({"outcome": "not_now"} if invalid == "not_now" else {})
    )
    assert result.status_code == 409, result.text
    assert not rows(db, CapacityProposal, case[3])


@pytest.mark.parametrize("percent", [30, 50])
def test_extension_counts_other_segment(
    case: tuple, client: TestClient, db: Session, percent: int
) -> None:
    url, _, auth, demand = case
    parts = segments(case, split=True)
    parts[0]["allocation_percent"] = percent
    parts[1]["allocation_percent"] = percent
    reply = propose(client, case, plan(client, case, parts))
    assert reply.status_code == 200, reply.text
    assert respond(client, case, reply.json()["revision"]).status_code == 200
    assigned = sorted(rows(db, CapacityAssignment, demand), key=lambda a: a.start_date)
    request = CapacityExtensionRequest(
        org_id=demand.org_id,
        assignment_id=assigned[0].id,
        requested_due_date=demand.window_end,
        reason="合成延期",
    )
    db.add(request)
    db.commit()
    response = client.post(
        f"{url}/capacity/extension-requests/{request.id}/decision",
        headers=auth["b"],
        json={"approve": True},
    )
    assert response.status_code == (409 if percent == 50 else 200), response.text
    db.expire_all()
    assigned = sorted(rows(db, CapacityAssignment, demand), key=lambda a: a.start_date)
    assert assigned[0].due_date == (
        demand.window_start + timedelta(days=6) if percent == 50 else demand.window_end
    )
    assert assigned[1].due_date == demand.window_end
    weekly = loads(
        db, demand.org_id, [assigned[0].employee_id], demand.window_start, 2
    )[assigned[0].employee_id]
    assert [w.platform_percent for w in weekly] == (
        [50, 50] if percent == 50 else [30, 60]
    )


def test_holds_invalidate_other_snapshot_and_own_simulation_excludes_them(
    case: tuple, client: TestClient, db: Session
) -> None:
    from app.domains.capacity.models import CapacityDemand

    url, people, auth, demand = case
    other = CapacityDemand(**demand.model_dump(exclude={"id", "created_at"}))
    db.add(other)
    db.commit()
    other_case = client.post(
        f"{url}/planning/demands/{other.id}/cases", headers=auth["b"]
    ).json()
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    assert (
        client.get(
            f"{url}/planning/cases/{other_case['id']}", headers=auth["b"]
        ).json()["stale"]
        is True
    )
    refreshed = client.post(
        f"{url}/planning/demands/{other.id}/cases", headers=auth["b"]
    ).json()
    person = next(
        p
        for p in refreshed["data"]["candidates"]
        if p["employee_id"] == str(people["E04"].id)
    )
    assert person["weeks"][0]["hold_percent"] == 50
    own = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    ).json()
    person = next(
        p
        for p in own["data"]["candidates"]
        if p["employee_id"] == str(people["E04"].id)
    )
    assert person["weeks"][0]["hold_percent"] == 0


def test_overlapping_lines_are_checked_as_a_sum(
    case: tuple, client: TestClient, db: Session
) -> None:
    parts = segments(case, percent=50) * 2
    saved = plan(client, case, parts)
    assert saved["verdict"] == "infeasible"
    # Even a stored verdict cannot bypass the current capacity recheck.
    row = db.get(PlanningOption, UUID(saved["id"]))
    row.verdict = "conditional"
    db.add(row)
    db.commit()
    response = propose(client, case, saved)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["over_weeks"][0]["over_percent"] == 10
    assert not rows(db, CapacityProposal, case[3])


def test_commit_audit_notifications_and_privacy(
    case: tuple, client: TestClient, db: Session
) -> None:
    from app.domains.audit.models import AuditEvent
    from app.domains.inbox.models import InboxItem

    url, people, auth, demand = case
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    assert respond(client, case, reply.json()["revision"]).status_code == 200
    events = rows(db, AuditEvent, demand)
    assert {e.action for e in events} >= {
        "capacity.demand_replied",
        "capacity.demand_accepted",
        "capacity.assignment_created",
    }
    notices = rows(db, InboxItem, demand)
    assert people["E04"].id in {n.recipient_employee_id for n in notices}
    row = proposal_row(db, demand)
    assert {row.proposed_by_user_id, demand.requester_user_id} <= {
        n.recipient_user_id for n in notices
    }
    a = client.get(f"{url}/capacity/demands/{demand.id}", headers=auth["a"])
    assert (
        str(people["E04"].id) not in a.text and people["E04"].display_name not in a.text
    )


def test_m9_migration_roundtrip_with_seed(
    migration_engine: Engine, seed_path: Path
) -> None:
    import sqlalchemy as sa

    from app.core.db import init_db
    from app.seed.loader import load_demo
    from tests.utils.migrations import migrate

    migrate(migration_engine, "downgrade", "c696001bc001")
    with Session(migration_engine) as session:
        init_db(session)
        session.commit()
        load_demo(session, seed_path)
        before = session.exec(select(OrgMembership.id)).all()
    migrate(migration_engine, "upgrade", "head")
    migrate(migration_engine, "downgrade", "c696001bc001")
    assert "capacity_proposal" not in sa.inspect(migration_engine).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    with Session(migration_engine) as session:
        assert session.exec(select(OrgMembership.id)).all() == before
    assert "capacity_proposal" in sa.inspect(migration_engine).get_table_names()


def test_downgrade_refuses_multiple_active_segments(
    migration_engine: Engine, seed_path: Path
) -> None:
    from app.core.db import init_db
    from app.domains.capacity.models import CapacityDemand
    from app.domains.org.models import Employee
    from app.seed.loader import load_demo
    from tests.utils.migrations import migrate

    with Session(migration_engine) as session:
        init_db(session)
        session.commit()
        load_demo(session, seed_path)
        employee = session.exec(select(Employee)).first()
        member = session.exec(
            select(OrgMembership).where(OrgMembership.org_id == employee.org_id)
        ).first()
        start = utc_now().date()
        demand = CapacityDemand(
            org_id=employee.org_id,
            requesting_department_id=employee.department_id,
            target_department_id=employee.department_id,
            requester_user_id=member.user_id,
            capability_key="synthetic",
            capability_name="合成能力",
            headcount=1,
            allocation_percent=50,
            window_start=start,
            window_end=start,
            description="合成迁移需求",
        )
        session.add(demand)
        session.flush()
        row = CapacityProposal(
            org_id=employee.org_id,
            demand_id=demand.id,
            source_option_id=employee.id,
            proposed_by_user_id=member.user_id,
            demand_revision=1,
            hold_expires_at=utc_now() + timedelta(hours=72),
            status="committed",
        )
        session.add(row)
        session.flush()
        for percent in (30, 50):
            line = CapacityProposalLine(
                org_id=employee.org_id,
                proposal_id=row.id,
                employee_id=employee.id,
                start_date=start,
                end_date=start,
                allocation_percent=percent,
            )
            session.add(line)
            session.flush()
            session.add(
                CapacityAssignment(
                    org_id=employee.org_id,
                    demand_id=demand.id,
                    employee_id=employee.id,
                    proposal_line_id=line.id,
                    allocation_percent=percent,
                    start_date=start,
                    due_date=start,
                    assigned_by_user_id=member.user_id,
                )
            )
        session.commit()
        before = [
            r.model_dump() for r in session.exec(select(CapacityAssignment)).all()
        ]
    with pytest.raises(RuntimeError, match="有 1 组.*多段"):
        migrate(migration_engine, "downgrade", "c696001bc001")
    with Session(migration_engine) as session:
        assert [
            r.model_dump() for r in session.exec(select(CapacityAssignment)).all()
        ] == before
        assert session.exec(select(CapacityProposal)).one().status == "committed"


def test_accept_conflict_hides_unreadable_demand_source(
    case: tuple, client: TestClient, db: Session
) -> None:
    from app.domains.capacity.models import CapacityDemand

    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    _, people, _, demand = case
    proposed = proposal_row(db, demand)
    other = CapacityDemand(**demand.model_dump(exclude={"id", "created_at"}))
    other.requester_user_id = proposed.proposed_by_user_id
    other.requesting_department_id = demand.target_department_id
    other.description = "PRIVATE_OTHER_DEMAND"
    other.status = "accepted"
    db.add(other)
    db.flush()
    db.add(
        CapacityAssignment(
            org_id=demand.org_id,
            demand_id=other.id,
            employee_id=people["E04"].id,
            allocation_percent=60,
            start_date=demand.window_start,
            due_date=demand.window_end,
            assigned_by_user_id=proposed.proposed_by_user_id,
        )
    )
    db.commit()
    response = respond(client, case, reply.json()["revision"])
    assert response.status_code == 409, response.text
    assert "其他需求" in response.text
    for secret in (
        "PRIVATE_OTHER_DEMAND",
        str(people["E04"].id),
        people["E04"].display_name,
    ):
        assert secret not in response.text
    assert proposal_row(db, demand).fail_reason == "recheck_failed"


def test_outside_basis_requires_explicit_confirmation(
    case: tuple, client: TestClient, db: Session
) -> None:
    response = propose(client, case, outside_basis=False)
    assert response.status_code == 409 and "依据" in response.text
    assert not rows(db, CapacityProposal, case[3])


def test_rejection_reason_limit_and_revision(
    case: tuple, client: TestClient, db: Session
) -> None:
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    before = counts(db, case[3])
    result = respond(
        client, case, reply.json()["revision"], "follow_up", text="合" * 501
    )
    assert result.status_code == 409
    result = respond(client, case, reply.json()["revision"] + 1)
    assert (
        result.status_code == 409
        and result.json()["detail"]["code"] == "proposal_decided"
    )
    assert counts(db, case[3]) == before
    assert proposal_row(db, case[3]).status == "pending"


def test_proposal_withdraw_is_tenant_and_resource_scoped(
    case: tuple, client: TestClient, db: Session
) -> None:
    url, _, auth, demand = case
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    before = counts(db, demand)
    for role in ("foreign", "employee", "a"):
        result = client.post(
            f"{url}/capacity/proposals/{reply.json()['proposal']['id']}/withdraw",
            headers=auth[role],
            json={"revision": 1},
        )
        assert result.status_code == 404
    assert counts(db, demand) == before


def test_mcp_cannot_approve_pending_proposal(
    case: tuple, client: TestClient, db: Session
) -> None:
    from app.domains.capacity.demand import respond_demand
    from app.domains.capacity.settings import CapacityForbidden

    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    before = counts(db, case[3])
    actor = proposal.current_principal(db, case[3].org_id, case[3].requester_user_id)
    with pytest.raises(CapacityForbidden):
        respond_demand(
            actor, db, case[3].id, "accept", reply.json()["revision"], via="mcp"
        )
    db.rollback()
    assert counts(db, case[3]) == before
    assert proposal_row(db, case[3]).status == "pending"
