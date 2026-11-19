from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session

from app.domains.capacity.demand_schemas import ChangeProposalParticipant
from app.domains.capacity.models import CapacityApproval
from app.domains.capacity.proposal import _aligned_participants
from tests.api.routes.test_m9_proposal import case as case
from tests.api.routes.test_m9_proposal import planning_setup as planning_setup
from tests.api.routes.test_m9_proposal import propose, respond, rows


def test_history_keeps_superseded_decisions_and_respects_scope(
    case: tuple, client: TestClient, db: Session
) -> None:
    url, people, auth, demand = case
    path = f"{url}/capacity/demands/{demand.id}/history"
    private_condition = "资源部门内部答复条件"
    statements: list[str] = []

    def record_query(*args: Any) -> None:
        statements.append(args[2])

    def read_history() -> Any:
        bind = db.get_bind()
        statements.clear()
        event.listen(bind, "before_cursor_execute", record_query)
        try:
            result = client.get(path, headers=auth["a"])
        finally:
            event.remove(bind, "before_cursor_execute", record_query)
        for table in ("capacity_proposal_line", "capacity_approval"):
            assert sum(f"FROM {table} " in sql for sql in statements) == 1
        return result

    offer = propose(client, case, text=private_condition)
    assert offer.status_code == 200, offer.text
    rejection = respond(
        client,
        case,
        offer.json()["revision"],
        "follow_up",
        reason_code="timing",
        text="请调整交付时间",
    )
    assert rejection.status_code == 200, rejection.text
    assert len(read_history().json()["proposals"]) == 1
    db.refresh(demand)
    later = propose(client, case)
    assert later.status_code == 200, later.text
    declined = next(
        a for a in rows(db, CapacityApproval, demand) if a.decision == "rejected"
    )
    declined.reason_code = None  # A rejection written before A4.
    db.add(declined)
    db.commit()

    outgoing = read_history()
    assert outgoing.status_code == 200
    body = outgoing.json()
    assert body["snapshots"] == []
    assert [p["id"] for p in body["proposals"]] == [
        offer.json()["proposal"]["id"],
        later.json()["proposal"]["id"],
    ]
    failed, pending = body["proposals"]
    assert failed["status"] == "failed" and failed["fail_reason"] == "rejected"
    assert pending["status"] == "pending"
    decision = failed["approvals"][-1]
    assert decision["party"] == "requester" and decision["decision"] == "rejected"
    assert decision["reason_code"] is None and decision["reason"] == "请调整交付时间"
    assert [event["kind"] for event in body["events"]] == [
        "replied",
        "proposal_failed",
        "replied",
    ]
    for secret in (private_condition, "employee_id", "hold_percent", "other_percent"):
        assert secret not in outgoing.text
    for person in (people["E04"], people["E05"]):
        assert (
            str(person.id) not in outgoing.text
            and person.display_name not in outgoing.text
        )

    incoming = client.get(path, headers=auth["b"])
    assert incoming.status_code == 200 and private_condition in incoming.text
    snapshots = incoming.json()["snapshots"]
    assert len(snapshots) == 2
    assert all(snapshot["options"] for snapshot in snapshots)
    assert all(
        "kind_label" in option and "verdict_label" in option
        for snapshot in snapshots
        for option in snapshot["options"]
    )
    for role in ("employee", "foreign"):
        assert client.get(path, headers=auth[role]).status_code == 404


@pytest.mark.parametrize(("removed", "added"), [(1, 1), (2, 2), (1, 2), (2, 1)])
def test_participant_numbers_only_reused_for_one_to_one(
    removed: int, added: int
) -> None:
    unchanged = uuid4()
    prev, current = _aligned_participants(
        {**{uuid4(): [] for _ in range(removed)}, unchanged: []},
        {unchanged: [], **{uuid4(): [] for _ in range(added)}},
    )
    previous_statuses = [
        (p.number, p.status) for p in prev if isinstance(p, ChangeProposalParticipant)
    ]
    current_statuses = [
        (p.number, p.status)
        for p in current
        if isinstance(p, ChangeProposalParticipant)
    ]
    if removed == added == 1:
        assert (
            previous_statuses == current_statuses == [(1, "replaced"), (2, "unchanged")]
        )
    else:
        assert previous_statuses == [(n, "removed") for n in range(1, removed + 1)] + [
            (removed + 1, "unchanged")
        ]
        assert current_statuses == [(removed + 1, "unchanged")] + [
            (n, "added") for n in range(removed + 2, removed + 2 + added)
        ]
