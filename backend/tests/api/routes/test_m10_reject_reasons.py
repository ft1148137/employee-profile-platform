import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.domains.bot import schemas, store, tools
from app.domains.capacity.calendar import loads
from app.domains.capacity.models import (
    CapacityApproval,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityProposal,
    CapacityProposalLine,
)
from tests.api.routes.test_m9_borrow import decide
from tests.api.routes.test_m9_borrow import offered as offered
from tests.api.routes.test_m9_proposal import case as case
from tests.api.routes.test_m9_proposal import planning_setup as planning_setup
from tests.api.routes.test_m9_proposal import propose, rows
from tests.api.routes.test_m9_proposal_entries import click
from tests.api.routes.test_mcp import call, initialize, token_for
from tests.domains.test_bot_core import enable
from tests.domains.test_im_gateway import principal


def state(db: Session, demand: CapacityDemand) -> tuple:
    db.expire_all()
    lines = rows(db, CapacityProposalLine, demand)
    holds = loads(
        db,
        demand.org_id,
        [line.employee_id for line in lines if line.employee_id is not None],
        demand.window_start,
        2,
    )
    return (
        demand.revision,
        demand.status,
        *(
            [row.model_dump() for row in rows(db, model, demand)]
            for model in (CapacityProposal, CapacityApproval, CapacityDemandEvent)
        ),
        {
            employee: [week.hold_percent for week in weeks]
            for employee, weeks in holds.items()
        },
    )


def rejection_payload(client: TestClient, case: tuple, operation: str) -> dict:
    if operation == "reply":
        return {
            "outcome": "not_now",
            "revision": case[3].revision,
            "text": "本期无法承接",
        }
    offered = propose(client, case)
    assert offered.status_code == 200, offered.text
    return {
        "action": "follow_up",
        "revision": offered.json()["revision"],
        "text": "需要调整交付日期",
    }


def invalid_payloads(body: dict) -> tuple[dict, dict]:
    return body, {**body, "reason_code": "other", "text": " "}


def stored_reason(db: Session, demand: CapacityDemand, operation: str) -> str | None:
    if operation == "reply":
        return next(
            e.reason_code
            for e in rows(db, CapacityDemandEvent, demand)
            if e.outcome == "not_now"
        )
    return next(
        a.reason_code
        for a in rows(db, CapacityApproval, demand)
        if a.decision == "rejected"
    )


@pytest.mark.parametrize("transport", ["rest", "mcp"])
@pytest.mark.parametrize(
    ("operation", "role", "person"), [("reply", "b", "E05"), ("respond", "a", "E01")]
)
def test_rejection_reasons_in_api(
    case: tuple,
    db: Session,
    client: TestClient,
    transport: str,
    operation: str,
    role: str,
    person: str,
) -> None:
    url, people, auth, demand = case
    body = rejection_payload(client, case, operation)
    headers = (
        initialize(client, token_for(db, people[person])[0])
        if transport == "mcp"
        else auth[role]
    )

    def send(payload: dict, *, valid: bool) -> None:
        if transport == "mcp":
            result = call(
                client,
                headers,
                f"{operation}_demand",
                {**payload, "demand_id": str(demand.id)},
            )
            assert result.get("isError", False) is (not valid), result
        else:
            result = client.post(
                f"{url}/capacity/demands/{demand.id}/{operation}",
                headers=headers,
                json=payload,
            )
            assert result.status_code == (200 if valid else 422), result.text

    before = state(db, demand)
    for payload in invalid_payloads(body):
        send(payload, valid=False)
        assert state(db, demand) == before
    send({**body, "reason_code": "other"}, valid=True)
    db.expire_all()
    assert stored_reason(db, demand, operation) == "other"


@pytest.mark.parametrize(
    ("role", "action"), [("lender", "reject"), ("worker", "decline")]
)
def test_borrow_rejection_reason(
    case: tuple, offered: tuple, db: Session, client: TestClient, role: str, action: str
) -> None:
    _, line, worker = offered
    demand = case[3]
    if role == "worker":
        assert (
            decide(
                client,
                case,
                line,
                "lender",
                action="select",
                employee_id=str(worker.id),
            ).status_code
            == 204
        )
    before = state(db, demand)
    for fields in ({}, {"reason_code": "other"}):
        response = decide(client, case, line, role, action=action, **fields)
        assert response.status_code == 422, response.text
        assert state(db, demand) == before
    explanation = {"reason": "本期已有安排"} if role == "lender" else {}
    result = decide(
        client, case, line, role, action=action, reason_code="priority", **explanation
    )
    assert result.status_code == 204, result.text
    db.expire_all()
    assert stored_reason(db, demand, "respond") == "priority"
    url, _, auth, _ = case
    for side in ("a", "b"):
        history = client.get(
            f"{url}/capacity/demands/{demand.id}/history", headers=auth[side]
        )
        assert history.status_code == 200
        (proposal,) = history.json()["proposals"]
        assert proposal["fail_reason"] == "borrow_unsuccessful"
        assert not any(
            approval["decision"] == "rejected" for approval in proposal["approvals"]
        )
    if role == "worker":
        mine = client.get(
            f"{url}/capacity/borrow-requests", headers=auth[role], params={"mine": True}
        )
        assert mine.status_code == 200 and mine.json()[0]["reason_code"] == "priority"


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize(
    ("operation", "person"), [("reply", "E05"), ("respond", "E01")]
)
def test_rejection_reasons_in_confirmation_cards(
    case: tuple,
    db: Session,
    client: TestClient,
    channel: str,
    operation: str,
    person: str,
) -> None:
    _, people, _, demand = case
    body = {**rejection_payload(client, case, operation), "demand_id": str(demand.id)}
    actor = principal(db, demand.org_id, people[person].id)
    enable(db, demand.org_id)
    session = store.create_session(db, actor, channel=channel)
    name = "prepare_demand_reply" if operation == "reply" else "prepare_demand_response"
    before = state(db, demand)
    cards: list[schemas.Message] = []
    for payload in invalid_payloads(body):
        result = tools.call(db, actor, session, cards, name, payload)
        assert isinstance(result, dict) and "error" in result and not cards
        assert state(db, demand) == before

    def save_card() -> schemas.CapacityCard:
        result = tools.call(
            db, actor, session, cards, name, {**body, "reason_code": "timing"}
        )
        assert isinstance(result, dict) and result.get("status") == "pending", result
        card = cards[-1]
        assert isinstance(card, schemas.CapacityCard) and "时间不合适" in card.content
        return card

    def confirm(card: schemas.CapacityCard) -> schemas.CapacityCard:
        store.append_message(
            db,
            session,
            "assistant",
            schemas.TurnResponse(messages=[card]).model_dump(mode="json"),
        )
        db.commit()
        return click(db, actor, session, card, client)

    old_card = save_card()
    del old_card.arguments["reason_code"]
    rejected = confirm(old_card)
    assert rejected.status == "failed" and "请重新出卡" in (rejected.result or "")
    assert state(db, demand) == before
    assert confirm(save_card()).status == "confirmed"
    db.expire_all()
    assert stored_reason(db, demand, operation) == "timing"
