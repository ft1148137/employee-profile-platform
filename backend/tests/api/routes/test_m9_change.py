"""Change proposals replace committed segments in one PostgreSQL transaction."""

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import event
from sqlmodel import Session, select

from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.bot.schemas import Message
from app.domains.capacity import assistant, proposal
from app.domains.capacity.calendar import loads
from app.domains.capacity.models import (
    CapacityApproval,
    CapacityAssignment,
    CapacityDemandEvent,
    CapacityExtensionRequest,
    CapacityProposal,
    CapacityProposalLine,
    CapacityWeek,
)
from app.domains.inbox.models import InboxItem
from tests.api.routes.test_m9_borrow import decide
from tests.api.routes.test_m9_borrow import offered as offered
from tests.api.routes.test_m9_proposal import case as case
from tests.api.routes.test_m9_proposal import (
    plan,
    propose,
    respond,
    rows,
    segments,
)
from tests.api.routes.test_m9_proposal import planning_setup as planning_setup
from tests.api.routes.test_m9_proposal_concurrency import race


@pytest.fixture
def committed(client: TestClient, case: tuple, db: Session) -> list[CapacityAssignment]:
    reply = propose(client, case, plan(client, case, segments(case, split=True)))
    assert reply.status_code == 200, reply.text
    accepted = respond(client, case, reply.json()["revision"])
    assert accepted.status_code == 200, accepted.text
    db.refresh(case[3])
    return sorted(rows(db, CapacityAssignment, case[3]), key=lambda row: row.due_date)


def offer_change(client: TestClient, case: tuple, merge: bool) -> Response:
    lines = segments(case, percent=40) if merge else segments(case, split=True)
    lines[-1]["allocation_percent"] = 40
    return submit_change(client, case, plan(client, case, lines))


def submit_change(
    client: TestClient, case: tuple, option: dict, **extra: Any
) -> Response:
    url, _, auth, demand = case
    assert option["verdict"] in ("feasible", "conditional"), option
    return client.post(
        f"{url}/capacity/demands/{demand.id}/change",
        headers=auth["b"],
        json={
            "option_id": option["id"],
            "reason": "本次调整投入",
            "revision": demand.revision,
            "outside_basis": True,
            **extra,
        },
    )


@pytest.mark.parametrize(
    "merge", [False, True], ids=["second-segment", "merge-segments"]
)
@pytest.mark.parametrize("attempt", ["accept", "rollback", "concurrent"])
def test_changes_replace_only_different_segments(
    client: TestClient,
    case: tuple,
    db: Session,
    committed: list[CapacityAssignment],
    merge: bool,
    attempt: str,
) -> None:
    url, people, auth, demand = case
    first, second = committed
    before = {row.id: row.model_dump() for row in committed}
    previous_dates = demand.committed_start_date, demand.committed_end_date
    previous_proposal = rows(db, CapacityProposal, demand)[0].id
    offer = offer_change(client, case, merge)
    assert offer.status_code == 200, offer.text
    pending = offer.json()
    assert pending["status"] == "accepted"
    proposal_id = pending["proposal"]["id"]
    detail = client.get(f"{url}/capacity/demands/{demand.id}", headers=auth["a"])
    assert detail.status_code == 200, detail.text
    assert str(people["E04"].id) not in detail.text
    assert people["E04"].display_name not in detail.text
    anonymous = detail.json()["proposal"]
    assert anonymous["previous"]["lines"] is None
    assert [
        s["allocation_percent"]
        for s in anonymous["previous"]["participants"][0]["segments"]
    ] == [30, 50]
    path = f"{url}/capacity/demands/{demand.id}"
    manager = client.get(path, headers=auth["b"])
    assert people["E04"].display_name in manager.text
    assert client.get(path, headers=auth["foreign"]).status_code == 404
    p = proposal.current_principal(
        db, demand.org_id, rows(db, CapacityProposal, demand)[0].proposed_by_user_id
    )
    assert proposal.get_demand(p, db, demand.id).proposal.previous is None

    def accept() -> Response:
        return respond(client, case, pending["revision"])

    if attempt == "rollback":

        def inject(_mapper: Any, _connection: Any, _target: CapacityAssignment) -> None:
            assert (
                _connection.execute(
                    select(CapacityAssignment.status).where(
                        CapacityAssignment.org_id == demand.org_id,
                        CapacityAssignment.id == second.id,
                    )
                ).scalar_one()
                == "cancelled"
            )
            raise RuntimeError("synthetic replacement insert failure")

        event.listen(CapacityAssignment, "before_insert", inject)
        try:
            with pytest.raises(RuntimeError, match="synthetic replacement insert"):
                accept()
        finally:
            event.remove(CapacityAssignment, "before_insert", inject)

        db.expire_all()
        assert {
            row.id: row.model_dump() for row in rows(db, CapacityAssignment, demand)
        } == before
        assert (
            demand.committed_start_date,
            demand.committed_end_date,
        ) == previous_dates
        proposals = {str(row.id): row for row in rows(db, CapacityProposal, demand)}
        assert proposals[str(previous_proposal)].status == "committed"
        assert proposals[proposal_id].status == "pending"
        assert not any(
            row.party == "requester" and str(row.proposal_id) == proposal_id
            for row in rows(db, CapacityApproval, demand)
        )
        assert all(
            row.kind != "commitment_changed"
            for row in rows(db, CapacityDemandEvent, demand)
        )
        return

    results = race(accept, accept) if attempt == "concurrent" else [accept()]
    assert sorted(r.status_code for r in results) == (
        [200, 409] if attempt == "concurrent" else [200]
    ), [r.text for r in results]
    db.expire_all()
    assignments = {row.id: row for row in rows(db, CapacityAssignment, demand)}
    assert len(assignments) == 3
    assert assignments[second.id].status == "cancelled"
    assert assignments[second.id].closed_at is not None
    assert assignments[first.id].status == ("cancelled" if merge else "active")
    if not merge:
        assert str(assignments[first.id].proposal_line_id) != str(
            before[first.id]["proposal_line_id"]
        )
        assert assignments[first.id].model_dump(exclude={"proposal_line_id"}) == {
            key: value
            for key, value in before[first.id].items()
            if key != "proposal_line_id"
        }
    else:
        assert assignments[first.id].closed_at is not None
    (new,) = (row for row in assignments.values() if row.id not in before)
    assert new.status == "active"
    assert new.employee_id == people["E04"].id
    assert new.allocation_percent == 40
    assert new.start_date == (first.start_date if merge else second.start_date)
    assert new.due_date == second.due_date
    line_proposals = {
        row.id: str(row.proposal_id) for row in rows(db, CapacityProposalLine, demand)
    }
    assert all(
        line_proposals[row.proposal_line_id] == proposal_id
        for row in assignments.values()
        if row.status == "active"
    )
    assert (
        sum(
            row.kind == "commitment_changed"
            for row in rows(db, CapacityDemandEvent, demand)
        )
        == 1
    )


@pytest.mark.usefixtures("committed")
def test_change_load_reserves_only_the_increase(db: Session, case: tuple) -> None:
    _, people, _, demand = case
    previous = rows(db, CapacityProposal, demand)[0]
    pending = CapacityProposal(
        org_id=demand.org_id,
        demand_id=demand.id,
        source_option_id=previous.source_option_id,
        proposed_by_user_id=previous.proposed_by_user_id,
        demand_revision=demand.revision,
        hold_expires_at=utc_now() + timedelta(hours=1),
        kind="change",
        replaces_proposal_id=previous.id,
    )
    db.add(pending)
    db.flush()
    db.add_all(
        [
            CapacityProposalLine(
                org_id=demand.org_id,
                proposal_id=pending.id,
                employee_id=people["E04"].id,
                start_date=date.fromisoformat(line["start_date"]),
                end_date=date.fromisoformat(line["end_date"]),
                allocation_percent=percent,
            )
            for line, percent in zip(segments(case, split=True), (30, 60), strict=True)
        ]
    )
    db.commit()
    weeks = loads(db, demand.org_id, [people["E04"].id], demand.window_start, 2)[
        people["E04"].id
    ]
    assert [(w.platform_percent, w.hold_percent) for w in weeks] == [(30, 0), (60, 10)]


@pytest.mark.usefixtures("committed")
def test_change_simulation_excludes_the_current_commitment(
    client: TestClient, case: tuple
) -> None:
    lines = segments(case, split=True)
    lines[1]["allocation_percent"] = 60
    simulated = plan(client, case, lines)
    assert simulated["verdict"] == "conditional", simulated["findings"]


@pytest.mark.usefixtures("committed")
def test_change_can_end_before_the_original_window(
    client: TestClient, case: tuple
) -> None:
    simulated = plan(client, case, segments(case, split=True)[:1])
    assert simulated["verdict"] == "conditional", simulated["findings"]


def test_change_can_reduce_committed_headcount(
    client: TestClient, case: tuple, db: Session
) -> None:
    demand = case[3]
    demand.headcount = 2
    db.commit()
    reply = propose(client, case, plan(client, case, segments(case, people=2)))
    assert reply.status_code == 200, reply.text
    accepted = respond(client, case, reply.json()["revision"])
    assert accepted.status_code == 200, accepted.text
    simulated = plan(client, case, segments(case))
    assert simulated["verdict"] == "conditional", simulated["findings"]


@pytest.mark.parametrize("body", [{"status": "cancelled"}, {"allocation_percent": 60}])
def test_direct_edits_of_a_committed_segment_require_a_change(
    client: TestClient,
    case: tuple,
    committed: list[CapacityAssignment],
    body: dict,
) -> None:
    url, _, auth, _ = case
    result = client.patch(
        f"{url}/capacity/assignments/{committed[0].id}", headers=auth["b"], json=body
    )
    assert result.status_code == 409 and "请发起变更" in result.text, result.text


def test_committed_start_moves_to_second_week_only_after_acceptance(
    client: TestClient, case: tuple, db: Session
) -> None:
    url, people, auth, demand = case
    lines = segments(case, people=2)
    first_week = demand.window_start
    second_week = first_week + timedelta(weeks=1)
    lines[1]["end_date"] = (second_week - timedelta(days=1)).isoformat()
    offer = propose(client, case, plan(client, case, lines))
    assert offer.status_code == 200, offer.text
    accepted = respond(client, case, offer.json()["revision"])
    assert accepted.status_code == 200, accepted.text
    db.expire_all()
    moved, covering = sorted(
        rows(db, CapacityAssignment, demand), key=lambda row: row.due_date, reverse=True
    )
    employee_id = people["E04"].id
    assert moved.employee_id == employee_id
    patch_path = f"{url}/capacity/assignments/{moved.id}"
    demand_path = f"{url}/capacity/demands/{demand.id}"
    before = client.get(demand_path, headers=auth["b"])
    assert before.status_code == 200, before.text
    original = moved.model_dump()
    assignment_updates = select(AuditEvent.id).where(
        AuditEvent.org_id == demand.org_id,
        AuditEvent.target_id == str(moved.id),
        AuditEvent.action == "capacity.assignment_updated",
    )
    assert not db.exec(assignment_updates).all()

    def weekly_load() -> list[int]:
        return [
            week.platform_percent
            for week in loads(db, demand.org_id, [employee_id], first_week, 2)[
                employee_id
            ]
        ]

    assert weekly_load() == [50, 50]
    update = {"start_date": second_week.isoformat()}
    for role in ("a", "foreign"):
        denied = client.patch(patch_path, headers=auth[role], json=update)
        assert denied.status_code == 404, denied.text
    rejected = client.patch(patch_path, headers=auth["b"], json=update)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"] == "已生效的安排请发起变更"
    db.refresh(moved)
    assert moved.model_dump() == original
    assert client.get(demand_path, headers=auth["b"]).json() == before.json()
    assert not db.exec(assignment_updates).all()
    assert weekly_load() == [50, 50]

    lines[0].update(update)
    change = submit_change(
        client, case, plan(client, case, lines), reason="调整开始日期"
    )
    assert change.status_code == 200, change.text
    db.refresh(moved)
    assert moved.model_dump() == original
    assert weekly_load() == [50, 50]

    accepted = respond(client, case, change.json()["revision"])
    assert accepted.status_code == 200, accepted.text
    db.expire_all()
    assert moved.status == "cancelled"
    assert covering.status == "active"
    (replacement,) = (
        row
        for row in rows(db, CapacityAssignment, demand)
        if row.employee_id == employee_id and row.status == "active"
    )
    assert replacement.id != moved.id
    assert replacement.start_date == second_week
    assert replacement.due_date == moved.due_date
    assert replacement.allocation_percent == moved.allocation_percent
    assert weekly_load() == [0, 50]
    assert [event["kind"] for event in accepted.json()["events"]].count(
        "commitment_changed"
    ) == 1


@pytest.mark.usefixtures("committed")
def test_assistant_directs_changes_to_the_web(db: Session, case: tuple) -> None:
    demand = case[3]
    requester = proposal.current_principal(db, demand.org_id, demand.requester_user_id)
    cards: list[Message] = []
    result = assistant.call(
        db,
        requester,
        cards,
        "prepare_demand_response",
        {"demand_id": str(demand.id), "action": "accept", "revision": demand.revision},
    )
    assert result == {"error": "请在网页发起或确认变更"}
    assert not cards


def test_extension_and_change_compete_for_the_same_segment(
    client: TestClient, case: tuple, db: Session, committed: list[CapacityAssignment]
) -> None:
    url, people, auth, demand = case
    first, second = committed
    first_id, second_id = first.id, second.id
    due = first.due_date + timedelta(days=1)
    request = CapacityExtensionRequest(
        org_id=demand.org_id,
        assignment_id=first_id,
        requested_due_date=due,
        reason="合成延期竞争",
    )
    db.add(request)
    db.commit()
    request_id = request.id
    offer = offer_change(client, case, merge=True)
    assert offer.status_code == 200, offer.text
    revision = offer.json()["revision"]
    accepted, extension = race(
        lambda: respond(client, case, revision),
        lambda: client.post(
            f"{url}/capacity/extension-requests/{request_id}/decision",
            headers=auth["b"],
            json={"approve": True},
        ),
    )
    assert sorted([accepted.status_code, extension.status_code]) == [200, 409], (
        accepted.text,
        extension.text,
    )
    db.expire_all()
    assignments = {row.id: row for row in rows(db, CapacityAssignment, demand)}
    if extension.status_code == 200:
        assert set(assignments) == {first_id, second_id}
        assert assignments[first_id].due_date == due
        assert request.status == "approved"
        assert accepted.json()["detail"]["code"] == "proposal_recheck_failed"
        db.refresh(demand)
        fresh = offer_change(client, case, merge=True)
        assert fresh.status_code == 200, fresh.text
        accepted = respond(client, case, fresh.json()["revision"])
        assert accepted.status_code == 200, accepted.text
    else:
        assert request.status == "pending"
    db.expire_all()
    assignments = {row.id: row for row in rows(db, CapacityAssignment, demand)}
    assert len(assignments) == 3
    assert assignments[first_id].status == assignments[second_id].status == "cancelled"
    assert sum(row.status == "active" for row in assignments.values()) == 1
    weekly = loads(db, demand.org_id, [people["E04"].id], demand.window_start, 2)[
        people["E04"].id
    ]
    assert [week.platform_percent for week in weekly] == [40, 40]
    assert all(
        week.platform_percent + (week.other_percent or 0) <= week.capacity_percent
        for week in weekly
    )
    assert (
        sum(
            e.kind == "commitment_changed"
            for e in rows(db, CapacityDemandEvent, demand)
        )
        == 1
    )


@pytest.mark.parametrize("outcome", ["rejected", "expired", "capacity", "accepted"])
def test_switch_person_changes_commitment_only_after_acceptance(
    client: TestClient,
    case: tuple,
    db: Session,
    committed: list[CapacityAssignment],
    outcome: str,
) -> None:
    url, people, auth, demand = case
    segment = segments(case)[0]
    segment["employee_id"] = str(people["E05"].id)
    segment["end_date"] = committed[0].due_date.isoformat()
    offer = submit_change(client, case, plan(client, case, [segment]))
    assert offer.status_code == 200, offer.text
    revision = offer.json()["revision"]
    before = {row.id: row.model_dump() for row in committed}
    old_end = demand.committed_end_date
    proposal_id = offer.json()["proposal"]["id"]
    pending = next(
        row for row in rows(db, CapacityProposal, demand) if str(row.id) == proposal_id
    )
    if outcome == "expired":
        pending.hold_expires_at = utc_now() - timedelta(minutes=1)
    if outcome == "capacity":
        for week in rows(db, CapacityWeek, demand):
            if week.employee_id == people["E05"].id:
                week.other_percent = 100
    db.commit()
    audit_ids = {row.id for row in rows(db, AuditEvent, demand)}
    notice_ids = {row.id for row in rows(db, InboxItem, demand)}
    result = (
        respond(client, case, revision, "follow_up", text="暂不接受这次变更")
        if outcome == "rejected"
        else respond(client, case, revision)
    )
    assert result.status_code == (
        200 if outcome in ("accepted", "rejected") else 409
    ), result.text
    db.expire_all()
    after = rows(db, CapacityAssignment, demand)
    assert demand.status == "accepted"
    if outcome != "accepted":
        assert {row.id: row.model_dump() for row in after} == before
        assert demand.committed_end_date == old_end
        assert pending.status == "failed"
        return
    (replacement,) = (row for row in after if row.status == "active")
    assert replacement.employee_id == people["E05"].id
    assert (demand.committed_end_date, replacement.due_date) == (
        committed[0].due_date,
    ) * 2
    assert all(row.status == "cancelled" for row in after if row.id in before)
    assert "capacity.commitment_changed" in {
        row.action for row in rows(db, AuditEvent, demand) if row.id not in audit_ids
    }
    notices = [row for row in rows(db, InboxItem, demand) if row.id not in notice_ids]
    assert {people["E04"].id, people["E05"].id} <= {
        row.recipient_employee_id for row in notices
    }
    assert {demand.requester_user_id, pending.proposed_by_user_id} <= {
        row.recipient_user_id for row in notices
    }
    detail = client.get(f"{url}/capacity/demands/{demand.id}", headers=auth["a"])
    assert people["E05"].display_name not in detail.text


def test_change_from_and_to_a_borrowed_employee(
    client: TestClient, case: tuple, db: Session, offered: tuple
) -> None:
    url, people, auth, demand = case
    initial, line, worker = offered
    selected = decide(
        client, case, line, "lender", action="select", employee_id=str(worker.id)
    )
    assert selected.status_code == 204, selected.text
    consent = decide(client, case, line, "worker", action="accept")
    assert consent.status_code == 204, consent.text
    accepted = respond(client, case, initial["revision"])
    assert accepted.status_code == 200, accepted.text
    db.refresh(demand)
    local = submit_change(client, case, plan(client, case))
    assert local.status_code == 200, local.text
    switched = respond(client, case, local.json()["revision"])
    assert switched.status_code == 200, switched.text
    db.expire_all()
    assert {
        (a.employee_id, a.status) for a in rows(db, CapacityAssignment, demand)
    } == {(worker.id, "cancelled"), (people["E04"].id, "active")}

    simulated = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert simulated.status_code == 200, simulated.text
    option = next(
        option for option in simulated.json()["options"] if option["kind"] == "borrow"
    )
    pending = submit_change(
        client,
        case,
        option,
        lender_department_id=str(worker.department_id),
        borrow_headcount=1,
    )
    assert pending.status_code == 200, pending.text
    proposal_id = pending.json()["proposal"]["id"]
    new_line = next(
        row
        for row in rows(db, CapacityProposalLine, demand)
        if str(row.proposal_id) == proposal_id
    )
    accepted = respond(client, case, pending.json()["revision"])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["proposal"]["status"] == "pending"
    picked = decide(
        client, case, new_line, "lender", action="select", employee_id=str(worker.id)
    )
    assert picked.status_code == 204, picked.text
    assert len(rows(db, CapacityAssignment, demand)) == 2
    for side in ("a", "b"):
        detail = client.get(f"{url}/capacity/demands/{demand.id}", headers=auth[side])
        assert (
            str(worker.id) not in detail.text and worker.display_name not in detail.text
        )
    consent = decide(client, case, new_line, "worker", action="accept")
    assert consent.status_code == 204, consent.text
    db.expire_all()
    active = [
        row for row in rows(db, CapacityAssignment, demand) if row.status == "active"
    ]
    assert [row.employee_id for row in active] == [worker.id]


def test_change_can_add_a_person_then_remove_them(
    client: TestClient, case: tuple, db: Session, committed: list[CapacityAssignment]
) -> None:
    demand = case[3]
    original = {row.id for row in committed}
    extra = segments(case, people=2)[1]
    initial_lines = segments(case, split=True)
    for lines, population in (([*initial_lines, extra], 2), (initial_lines, 1)):
        db.refresh(demand)
        offer = submit_change(client, case, plan(client, case, lines))
        assert offer.status_code == 200, offer.text
        accepted = respond(client, case, offer.json()["revision"])
        assert accepted.status_code == 200, accepted.text
        db.expire_all()
        active = [
            row
            for row in rows(db, CapacityAssignment, demand)
            if row.status == "active"
        ]
        assert len({row.employee_id for row in active}) == population
        assert original <= {row.id for row in active}
    assert len(rows(db, CapacityAssignment, demand)) == 3


@pytest.mark.usefixtures("committed")
def test_change_simulation_rejects_overbooking(client: TestClient, case: tuple) -> None:
    option = plan(client, case, segments(case, percent=100))
    assert option["verdict"] == "infeasible"
    assert "over_capacity" in {finding["code"] for finding in option["findings"]}
