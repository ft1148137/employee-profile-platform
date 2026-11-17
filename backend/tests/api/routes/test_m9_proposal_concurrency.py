"""Competing REST transactions use real PostgreSQL connections and row locks."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.capacity import proposal
from app.domains.capacity.calendar import loads
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityProposal,
)
from tests.api.routes.test_m9_proposal import (
    case as case,
)
from tests.api.routes.test_m9_proposal import (
    plan,
    proposal_row,
    propose,
    respond,
    rows,
    segments,
)
from tests.api.routes.test_m9_proposal import (
    planning_setup as planning_setup,
)


def race(*actions: Callable[[], Any]) -> list[Any]:
    barrier = Barrier(len(actions))

    def run(action: Callable[[], Any]) -> Any:
        barrier.wait(timeout=10)
        return action()

    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        futures = [pool.submit(run, action) for action in actions]
        return [future.result(timeout=30) for future in futures]


def another(db: Session, case: tuple) -> tuple:
    old = case[3]
    db.refresh(old)
    demand = CapacityDemand(**old.model_dump(exclude={"id", "created_at"}))
    demand.description = "另一条合成需求"
    db.add(demand)
    db.commit()
    return (*case[:3], demand)


@pytest.mark.parametrize("overlap", [False, True])
def test_competing_proposals_do_not_overbook(
    case: tuple, client: TestClient, db: Session, overlap: bool
) -> None:
    other = another(db, case)
    lines = segments(case, percent=60)
    if overlap:
        lines = segments(case, percent=30) + segments(case, percent=30)
    first = plan(client, case, lines)
    second = plan(client, other, segments(other, percent=60))
    results = race(
        lambda: propose(client, case, first), lambda: propose(client, other, second)
    )
    assert sorted(r.status_code for r in results) == [200, 409], [
        r.text for r in results
    ]
    rejected = next(r for r in results if r.status_code == 409)
    assert "sources" in rejected.text and "over_percent" in rejected.text
    assert (
        loads(db, case[3].org_id, [case[1]["E04"].id], case[3].window_start, 2)[
            case[1]["E04"].id
        ][0].platform_percent
        == 60
    )


@pytest.mark.parametrize("rival", ["accept", "withdraw", "expire"])
def test_accept_competes_with_one_terminal_transition(
    case: tuple, client: TestClient, db: Session, rival: str
) -> None:
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    url, _, auth, demand = case
    pid = reply.json()["proposal"]["id"]

    def competing() -> Any:
        if rival == "withdraw":
            return client.post(
                f"{url}/capacity/proposals/{pid}/withdraw",
                headers=auth["b"],
                json={"revision": 1},
            )
        if rival == "accept":
            return respond(client, case, reply.json()["revision"])
        with Session(db.get_bind()) as worker:
            count = proposal.expire(Principal.system_worker(demand.org_id), worker)
            worker.commit()
            return count

    results = race(lambda: respond(client, case, reply.json()["revision"]), competing)
    row = proposal_row(db, demand)
    assert row.status in ("committed", "withdrawn")
    assert len(rows(db, CapacityAssignment, demand)) == (
        1 if row.status == "committed" else 0
    )
    if rival != "expire":
        assert sorted(r.status_code for r in results) == [200, 409], [
            r.text for r in results
        ]


def test_accept_and_expired_worker_only_fail_once(
    case: tuple, client: TestClient, db: Session
) -> None:
    from datetime import timedelta

    from app.domains.base import utc_now
    from app.domains.capacity.models import CapacityDemandEvent

    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    row = proposal_row(db, case[3])
    row.hold_expires_at = utc_now() - timedelta(seconds=1)
    db.add(row)
    db.commit()
    org_id = case[3].org_id
    demand_id = case[3].id
    bind = db.get_bind()
    revision = reply.json()["revision"]

    def expire() -> int:
        with Session(bind) as session:
            result = proposal.expire(Principal.system_worker(org_id), session)
            session.commit()
            return result

    results = race(
        lambda: client.post(
            f"{case[0]}/capacity/demands/{demand_id}/respond",
            headers=case[2]["a"],
            json={"action": "accept", "revision": revision},
        ),
        expire,
    )
    assert results[0].status_code == 409
    assert proposal_row(db, case[3]).fail_reason == "expired"
    assert not rows(db, CapacityAssignment, case[3])
    assert (
        len(
            [
                e
                for e in rows(db, CapacityDemandEvent, case[3])
                if e.kind == "proposal_failed"
            ]
        )
        == 1
    )


def test_accept_and_other_assignment_extension_cannot_overbook(
    case: tuple, client: TestClient, db: Session
) -> None:
    from datetime import timedelta

    from app.domains.capacity.models import CapacityExtensionRequest

    url, people, auth, demand = case
    start = demand.window_start
    demand.window_start += timedelta(weeks=1)
    db.add(demand)
    db.commit()
    old = another(db, case)[3]
    old.status = "accepted"
    old.committed_start_date, old.committed_end_date = start, demand.window_end
    db.add(old)
    db.flush()
    member = proposal.current_principal(db, demand.org_id, demand.requester_user_id)
    assignment = CapacityAssignment(
        org_id=demand.org_id,
        demand_id=old.id,
        employee_id=people["E04"].id,
        allocation_percent=60,
        start_date=start,
        due_date=start + timedelta(days=6),
        assigned_by_user_id=member.user_id,
    )
    db.add(assignment)
    db.flush()
    request = CapacityExtensionRequest(
        org_id=demand.org_id,
        assignment_id=assignment.id,
        requested_due_date=demand.window_end,
        reason="合成延期竞争",
    )
    db.add(request)
    db.commit()
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    request_id = request.id
    results = race(
        lambda: respond(client, case, reply.json()["revision"]),
        lambda: client.post(
            f"{url}/capacity/extension-requests/{request_id}/decision",
            headers=auth["b"],
            json={"approve": True},
        ),
    )
    assert [r.status_code for r in results] == [200, 409], [r.text for r in results]
    assert "over_weeks" in results[1].text
    db.refresh(assignment)
    assert assignment.due_date == start + timedelta(days=6)


def test_propose_rechecks_latest_snapshot_after_employee_lock(
    case: tuple, client: TestClient, db: Session
) -> None:
    from threading import Event

    from sqlalchemy import event
    from sqlmodel import select

    from app.domains.org.models import Employee

    saved = plan(client, case)
    person_id, org_id = case[1]["E04"].id, case[3].org_id
    bind = db.get_bind()
    entered = Event()

    def observe(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _many: bool,
    ) -> None:
        if "FROM employee" in statement and "FOR UPDATE" in statement:
            entered.set()

    with Session(bind) as blocker:
        blocker.exec(
            select(Employee)
            .where(Employee.org_id == org_id, Employee.id == person_id)
            .with_for_update()
        ).one()
        event.listen(bind, "before_cursor_execute", observe)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(propose, client, case, saved)
                try:
                    assert entered.wait(timeout=10)
                    fresh = client.post(
                        f"{case[0]}/planning/demands/{case[3].id}/cases",
                        headers=case[2]["b"],
                    )
                    assert fresh.status_code == 200, fresh.text
                finally:
                    blocker.rollback()
                result = future.result(timeout=20)
        finally:
            event.remove(bind, "before_cursor_execute", observe)
    assert result.status_code == 409, result.text
    assert not rows(db, CapacityProposal, case[3])


def test_accept_rechecks_expiry_after_waiting_for_employee(
    case: tuple, client: TestClient, db: Session
) -> None:
    from datetime import timedelta
    from threading import Event

    from sqlalchemy import event
    from sqlmodel import select

    from app.domains.base import utc_now
    from app.domains.org.models import Employee

    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    row = proposal_row(db, case[3])
    expires = utc_now() + timedelta(seconds=5)
    row.hold_expires_at = expires
    db.add(row)
    db.commit()
    person_id, org_id = case[1]["E04"].id, case[3].org_id
    _ = case[3].id
    bind = db.get_bind()
    entered = Event()

    def observe(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _many: bool,
    ) -> None:
        if "FROM employee" in statement and "FOR UPDATE" in statement:
            entered.set()

    with Session(bind) as blocker:
        blocker.exec(
            select(Employee)
            .where(Employee.org_id == org_id, Employee.id == person_id)
            .with_for_update()
        ).one()
        event.listen(bind, "before_cursor_execute", observe)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(respond, client, case, reply.json()["revision"])
                try:
                    assert entered.wait(timeout=10)
                    Event().wait(max(0, (expires - utc_now()).total_seconds()) + 0.1)
                finally:
                    blocker.rollback()
                result = future.result(timeout=20)
        finally:
            event.remove(bind, "before_cursor_execute", observe)
    assert result.status_code == 409, result.text
    assert proposal_row(db, case[3]).fail_reason == "expired"
    assert not rows(db, CapacityAssignment, case[3])
