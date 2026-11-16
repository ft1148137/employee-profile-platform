from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.domains.capacity.calendar import this_week
from tests.api.routes.test_m6_capacity_workload import capacity as capacity
from tests.api.routes.test_m6_capacity_workload import enable


@pytest.mark.parametrize("approve", [True, False])
def test_weekly_correction(
    capacity: tuple, db: Session, client: TestClient, approve: bool
) -> None:
    url, people, auth = capacity
    enable(client, capacity)
    start = this_week(db, people["E02"].org_id)
    body = {
        "start_week": (start + timedelta(weeks=1)).isoformat(),
        "end_week": (start + timedelta(weeks=2)).isoformat(),
        "requested_percent": 40,
        "reason": "合成周修正",
    }
    endpoint = f"{url}/capacity"
    result = client.post(
        f"{endpoint}/workloads/me/requests", headers=auth["employee"], json=body
    )
    assert result.status_code == 200, result.text
    row = result.json()
    assert (row["start_week"], row["end_week"]) == (
        body["start_week"],
        body["end_week"],
    )

    def mine() -> dict:
        return client.get(f"{endpoint}/workloads/me", headers=auth["employee"]).json()

    assert list(mine()["correction_pending"].values()) == [
        False,
        True,
        True,
        False,
        False,
        False,
        False,
        False,
    ]
    assert (
        client.post(
            f"{endpoint}/workloads/me/requests", headers=auth["employee"], json=body
        ).status_code
        == 409
    )
    decision = f"{endpoint}/workload-requests/{row['id']}/decision"
    for role in ("employee", "foreign"):
        assert (
            client.post(
                decision, headers=auth[role], json={"approve": True}
            ).status_code
            == 404
        )
    response = client.post(
        decision, headers=auth["manager"], json={"approve": approve, "note": "合成说明"}
    )
    assert response.status_code == 200
    expected = [None, 40, 40, None, None, None, None, None] if approve else [None] * 8
    assert [w["other_percent"] for w in mine()["weeks"]] == expected
    assert not any(mine()["correction_pending"].values())


@pytest.mark.parametrize(
    "offset,end,missing",
    [(1, 7, False), (7, 0, False), (0, 182, False), (-7, -7, False), (0, 0, True)],
)
def test_invalid_weeks(
    capacity: tuple,
    db: Session,
    client: TestClient,
    offset: int,
    end: int,
    missing: bool,
) -> None:
    url, people, auth = capacity
    enable(client, capacity)
    start = this_week(db, people["E02"].org_id)
    body = {"requested_percent": 40, "reason": "合成修正"}
    if not missing:
        body.update(
            start_week=(start + timedelta(days=offset)).isoformat(),
            end_week=(start + timedelta(days=end)).isoformat(),
        )
    result = client.post(
        f"{url}/capacity/workloads/me/requests", headers=auth["employee"], json=body
    )
    assert result.status_code == 422, result.text


def test_assistant_preview_and_mcp_origin(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    from sqlmodel import select

    from app.domains.audit.models import AuditEvent
    from app.domains.bot import core, store
    from app.domains.capacity.models import CapacityWorkloadRequest
    from tests.api.routes.test_m6_capacity_assistant_reply import prepare
    from tests.api.routes.test_m6_capacity_mcp import use
    from tests.api.routes.test_mcp import initialize, token_for
    from tests.domains.test_im_gateway import principal

    _, people, _ = capacity
    enable(client, capacity)
    person = people["E02"]
    p = principal(db, person.org_id, person.id)
    start = this_week(db, p.org_id).isoformat()
    body = {
        "start_week": start,
        "end_week": start,
        "requested_percent": 40,
        "reason": "合成修正",
    }
    session = store.create_session(db, p)
    card = prepare(db, p, session, "prepare_workload_request", body)
    assert f"周范围：{start} 至 {start}" in card.content
    assert not db.exec(
        select(CapacityWorkloadRequest).where(
            CapacityWorkloadRequest.org_id == p.org_id
        )
    ).all()
    assert (
        core.act(db, p, session.id, card.actions[0]).messages[0].status == "confirmed"
    )
    mcp = initialize(client, token_for(db, people["E01"])[0], name="周修正客户端")
    result = use(client, mcp, "request_workload_change", body)
    assert result["start_week"] == start
    event = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == p.org_id, AuditEvent.target_id == result["id"]
        )
    ).one()
    assert (
        event.details["via"] == "mcp" and event.details["client_name"] == "周修正客户端"
    )


def test_legacy_preview_requires_new_card(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    from sqlmodel import select

    from app.domains.audit.models import AuditEvent
    from app.domains.bot import core, schemas, store
    from app.domains.capacity.models import CapacityWorkloadRequest
    from app.domains.inbox.models import InboxItem
    from tests.domains.test_im_gateway import principal

    _, people, _ = capacity
    enable(client, capacity)
    p = principal(db, people["E02"].org_id, people["E02"].id)
    card = schemas.CapacityCard(
        kind="workload_request",
        title="旧申请",
        content="旧预览",
        arguments={"requested_percent": 40, "reason": "合成旧申请"},
    )
    card.actions = [
        schemas.CapacityAction(action="confirm_capacity", card_id=card.card_id)
    ]
    session = store.create_session(db, p)
    store.append_message(
        db,
        session,
        "assistant",
        schemas.TurnResponse(messages=[card]).model_dump(mode="json"),
    )
    db.commit()
    models = (CapacityWorkloadRequest, AuditEvent, InboxItem)
    before = {
        model: set(db.exec(select(model.id).where(model.org_id == p.org_id)).all())
        for model in models
    }
    result = core.act(db, p, session.id, card.actions[0]).messages[0]
    assert result.status == "failed"
    assert result.result == "申请内容已变化，请重新生成确认卡"
    for model in models:
        assert (
            set(db.exec(select(model.id).where(model.org_id == p.org_id)).all())
            == before[model]
        )
