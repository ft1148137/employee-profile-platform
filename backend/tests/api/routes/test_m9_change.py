"""Change proposals replace committed segments in one PostgreSQL transaction."""

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import event
from sqlmodel import Session

from app.domains.base import utc_now
from app.domains.bot.schemas import Message
from app.domains.capacity import assistant, proposal
from app.domains.capacity.calendar import loads
from app.domains.capacity.models import (
    CapacityApproval,
    CapacityAssignment,
    CapacityDemandEvent,
    CapacityProposal,
    CapacityProposalLine,
)
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
    url, _, auth, demand = case
    lines = segments(case, percent=40) if merge else segments(case, split=True)
    lines[-1]["allocation_percent"] = 40
    option = plan(client, case, lines)
    assert option["verdict"] in ("feasible", "conditional"), option
    return client.post(
        f"{url}/capacity/demands/{demand.id}/change",
        headers=auth["b"],
        json={
            "option_id": option["id"],
            "reason": "本次调整投入",
            "revision": demand.revision,
            "outside_basis": True,
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

    def accept() -> Response:
        return respond(client, case, pending["revision"])

    if attempt == "rollback":

        def inject(_mapper: Any, _connection: Any, _target: CapacityAssignment) -> None:
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
