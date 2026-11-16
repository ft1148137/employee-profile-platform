import asyncio
import json
from collections.abc import Generator
from dataclasses import replace
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, delete, select

from app.authz.principal import Principal
from app.core.db import engine
from app.core.security import create_access_token
from app.domains.audit.models import AuditEvent
from app.domains.bot import core, store, tools
from app.domains.bot import schemas as s
from app.domains.bot.models import BotSession
from app.domains.capacity import assistant, demand
from app.domains.capacity.calendar import this_week
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityExtensionRequest,
    CapacitySetting,
    CapacityWorkloadRequest,
)
from app.domains.im import binding, gateway
from app.domains.im.adapters.feishu import Feishu
from app.domains.im.models import ImAction, ImSetting
from app.domains.org.models import Department, OrgMembership, RoleGrant
from tests.domains.test_bot_core import enable
from tests.domains.test_im_gateway import principal
from tests.utils.bot import final_response
from tests.utils.pi import PiHarness


@pytest.fixture
def case(demo: tuple, db: Session, client: TestClient) -> Generator[tuple]:
    url, people, headers = demo
    a = principal(db, people["E02"].org_id, people["E02"].id)
    target = Department(org_id=a.org_id, name="合成承接部门")
    db.add(target)
    db.flush()
    people["E05"].department_id = target.id
    people["E05"].display_name = "合成承接经理"
    db.add(people["E05"])
    b = principal(db, a.org_id, people["E05"].id)
    db.add(
        RoleGrant(
            org_id=a.org_id,
            membership_id=db.exec(
                select(OrgMembership.id).where(
                    OrgMembership.org_id == a.org_id, OrgMembership.user_id == b.user_id
                )
            ).one(),
            role="dept_manager",
            department_id=target.id,
        )
    )
    db.add(CapacitySetting(org_id=a.org_id, enabled=True, updated_by_user_id=a.user_id))
    db.commit()
    b = principal(db, a.org_id, people["E05"].id)
    capability = client.get(f"{url}/capabilities", headers=headers["admin"]).json()[
        "data"
    ][0]
    row = demand.submit_demand(
        a,
        db,
        target.id,
        capability["key"],
        people["E02"].department_id,
        1,
        50,
        date(2026, 12, 1),
        date(2026, 12, 31),
        "合成需求",
    )
    db.commit()
    args = {
        "demand_id": str(row.id),
        "revision": 1,
        "outcome": "can_do",
        "text": "部门可以承接",
        "start_date": "2026-12-01",
        "end_date": "2026-12-31",
    }
    yield a, b, args
    db.rollback()
    for model in (
        CapacityExtensionRequest,
        CapacityAssignment,
        CapacityDemandEvent,
        CapacityDemand,
        CapacityWorkloadRequest,
        CapacitySetting,
    ):
        db.exec(delete(model).where(model.org_id == a.org_id))
    db.commit()


def prepare(
    db: Session, p: Principal, session: BotSession, name: str, args: dict
) -> s.CapacityCard:
    cards = []
    result = tools.call(db, p, session, cards, name, args)
    assert isinstance(result, dict) and "error" not in result, result
    assert len(cards) == 1
    store.append_message(
        db, session, "assistant", s.TurnResponse(messages=cards).model_dump(mode="json")
    )
    db.commit()
    return cards[0]


def model_call(name: str, args: dict) -> dict:
    return {"calls": [{"name": name, "arguments": args}]}


def test_replaced_reply_card_is_rejected(case: tuple, db: Session) -> None:
    a, b, args = case
    session = store.create_session(db, b)
    old = prepare(db, b, session, "prepare_demand_reply", args)
    new = prepare(
        db, b, session, "prepare_demand_reply", {**args, "text": "修改后的答复"}
    )
    with pytest.raises(demand.CapacityConflict):
        core.act(db, b, session.id, old.actions[0])
    assert demand.get_demand(a, db, UUID(args["demand_id"])).revision == 1
    result = core.act(db, b, session.id, new.actions[0]).messages[0]
    assert result.status == "confirmed"
    assert "答复" in result.result
    assert (
        demand.get_demand(a, db, UUID(args["demand_id"])).events[-1].text
        == "修改后的答复"
    )


@pytest.mark.parametrize(
    "attack", ["requester", "scope", "tenant", "self", "name", "code", "stale"]
)
def test_reply_rejected_before_card(
    case: tuple, demo: tuple, db: Session, attack: str
) -> None:
    a, b, args = case
    p = b
    if attack == "requester":
        p = a
    elif attack == "scope":
        p = replace(b, managed_department_ids=frozenset())
    elif attack == "tenant":
        p = replace(b, org_id=uuid4())
    elif attack == "self":
        p = replace(b, user_id=a.user_id)
    elif attack in ("name", "code"):
        args = {
            **args,
            "text": getattr(
                demo[1]["E05"], "display_name" if attack == "name" else "employee_code"
            ),
        }
    else:
        args = {**args, "revision": 2}
    cards = []
    result = assistant.call(db, p, cards, "prepare_demand_reply", args)
    assert "error" in result and not cards
    assert demand.get_demand(a, db, UUID(args["demand_id"])).revision == 1


def assigned(db: Session, a: Principal, b: Principal, args: dict) -> CapacityAssignment:
    row = db.exec(
        select(CapacityDemand).where(
            CapacityDemand.org_id == a.org_id,
            CapacityDemand.id == UUID(args["demand_id"]),
        )
    ).one()
    row.status = "accepted"
    row.committed_start_date, row.committed_end_date = (
        date(2026, 12, 1),
        date(2026, 12, 31),
    )
    assignment = CapacityAssignment(
        org_id=a.org_id,
        demand_id=row.id,
        employee_id=b.employee_id,
        allocation_percent=50,
        due_date=date(2026, 12, 20),
        assigned_by_user_id=b.user_id,
    )
    db.add_all([row, assignment])
    db.commit()
    return assignment


@pytest.mark.parametrize("kind", ["workload_request", "extension_request"])
@pytest.mark.parametrize(
    "attack",
    [
        "employee_id",
        "org_id",
        "via",
        "no_employee",
        "tenant",
        "other_assignment",
        "pending",
    ],
)
def test_employee_prepare_attacks(
    case: tuple, db: Session, kind: str, attack: str
) -> None:
    a, b, args = case
    row = assigned(db, a, b, args)
    body = (
        {
            "start_week": this_week(db, a.org_id).isoformat(),
            "end_week": this_week(db, a.org_id).isoformat(),
            "requested_percent": 80,
            "reason": "合成原因",
        }
        if kind == "workload_request"
        else {
            "assignment_id": str(row.id),
            "requested_due_date": "2026-12-25",
            "reason": "合成原因",
        }
    )
    p = b
    if attack in ("employee_id", "org_id", "via"):
        body[attack] = str(a.employee_id)
    elif attack == "no_employee":
        p = replace(b, employee_id=None)
    elif attack == "tenant":
        p = replace(b, org_id=uuid4())
    elif attack == "other_assignment":
        if kind == "workload_request":
            body["employee_id"] = str(a.employee_id)
        else:
            p = a
    else:
        request = (
            CapacityWorkloadRequest(org_id=b.org_id, employee_id=b.employee_id, **body)
            if kind == "workload_request"
            else CapacityExtensionRequest(org_id=b.org_id, **body)
        )
        db.add(request)
        db.commit()
    cards = []
    result = assistant.call(db, p, cards, f"prepare_{kind}", body)
    assert "error" in result and not cards


@pytest.mark.parametrize("change", ["cancel", "disabled", "scope", "private"])
def test_reply_confirm_rechecks(
    case: tuple, demo: tuple, db: Session, change: str
) -> None:
    _, b, args = case
    session = store.create_session(db, b)
    card = prepare(db, b, session, "prepare_demand_reply", args)
    if change == "disabled":
        setting = db.exec(
            select(CapacitySetting).where(CapacitySetting.org_id == b.org_id)
        ).one()
        setting.enabled = False
        db.add(setting)
        db.commit()
    elif change == "scope":
        b = replace(b, managed_department_ids=frozenset())
    elif change == "private":
        demo[1]["E05"].display_name = "可以承接"
        db.add(demo[1]["E05"])
        db.commit()
    action = card.actions[1] if change == "cancel" else card.actions[0]
    result = core.act(db, b, session.id, action).messages[0]
    assert result.status == ("cancelled" if change == "cancel" else "failed")
    with pytest.raises(demand.CapacityConflict):
        core.act(db, b, session.id, card.actions[0])


def test_web_reply_revision_and_workload(
    case: tuple,
    demo: tuple,
    db: Session,
    client: TestClient,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, b, args = case
    enable(db, b.org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    headers = {
        "Authorization": f"Bearer {create_access_token(b.user_id, expires_delta=timedelta(minutes=5))}"
    }
    base, query = "/api/v1/bot/sessions", {"org_id": str(b.org_id)}
    session_id = client.post(base, params=query, headers=headers).json()["session_id"]

    def post(suffix: str, body: dict, auth: dict | None = None) -> Response:
        return client.post(
            f"{base}/{session_id}/{suffix}",
            params=query,
            headers=headers if auth is None else auth,
            json=body,
        )

    for text in ("部门可以承接", "修改后可以承接"):
        pi_service.replies = [
            {
                "calls": [
                    {"name": n, "arguments": {"demand_id": args["demand_id"]}}
                    for n in ("get_demand", "get_demand_basis")
                ]
            },
            model_call("prepare_demand_reply", {**args, "text": text}),
            "请核对答复",
        ]
        response = final_response(post("turns", {"text": text}))
        card = next(m for m in response["messages"] if m["type"] == "capacity_card")
        assert demo[1]["E05"].display_name not in card["content"]
        assert demo[1]["E05"].employee_code not in card["content"]
        assert demand.get_demand(a, db, UUID(args["demand_id"])).revision == 1
        if text == "部门可以承接":
            old = card
    for auth in (demo[2]["employee"], demo[2]["foreign"]):
        assert post("actions", card["actions"][0], auth).status_code == 404
    for action, status in (
        (old["actions"][0], 409),
        ({"action": "confirm_capacity", "card_id": str(uuid4())}, 409),
        ({**card["actions"][0], "employee_id": str(a.employee_id)}, 422),
    ):
        assert post("actions", action).status_code == status
    confirmed = post("actions", card["actions"][0])
    assert confirmed.status_code == 200
    assert confirmed.json()["messages"][0]["status"] == "confirmed"
    visible = assistant.call(db, a, [], "get_demand", {"demand_id": args["demand_id"]})
    raw = json.dumps(visible, ensure_ascii=False)
    assert "修改后可以承接" in raw and '"via": "bot"' in raw
    assert (
        demo[1]["E05"].display_name not in raw
        and demo[1]["E05"].employee_code not in raw
    )
    headers = demo[2]["employee"]
    session_id = client.post(base, params=query, headers=headers).json()["session_id"]
    pi_service.replies = [
        model_call(
            "prepare_workload_request",
            {
                "start_week": this_week(db, a.org_id).isoformat(),
                "end_week": this_week(db, a.org_id).isoformat(),
                "requested_percent": 80,
                "reason": "项目占用",
            },
        ),
        "请核对负荷申请",
    ]
    response = final_response(post("turns", {"text": "申请调整负荷"}))
    card = next(m for m in response["messages"] if m["type"] == "capacity_card")
    assert not db.exec(
        select(CapacityWorkloadRequest).where(
            CapacityWorkloadRequest.org_id == a.org_id
        )
    ).all()
    result = post("actions", card["actions"][0])
    assert result.json()["messages"][0]["status"] == "confirmed"
    event = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == a.org_id,
            AuditEvent.action == "capacity.workload_requested",
        )
    ).one()
    assert event.details["via"] == "bot"
    assert (
        db.exec(
            select(CapacityWorkloadRequest).where(
                CapacityWorkloadRequest.org_id == a.org_id
            )
        )
        .one()
        .employee_id
        == a.employee_id
    )


def test_feishu_extension_bound_confirmation(
    case: tuple,
    db: Session,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, b, args = case
    row = assigned(db, a, b, args)
    enable(db, b.org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    pi_service.replies = [
        model_call(
            "prepare_extension_request",
            {
                "assignment_id": str(row.id),
                "requested_due_date": "2026-12-25",
                "reason": "合成延期原因",
            },
        ),
        "请核对",
    ]
    setting = ImSetting(
        org_id=b.org_id,
        platform="feishu",
        enabled=True,
        credentials="synthetic-encrypted",
        bot_identity="synthetic-app",
        updated_by_user_id=b.user_id,
        email_auto_bind=False,
    )
    db.add(setting)
    db.commit()
    for p, user in ((a, "a"), (b, "b")):
        assert binding.bind(
            db,
            b.org_id,
            "feishu",
            user,
            "chat",
            "synthetic-app",
            binding.create_code(db, p).code,
        )

    async def exercise() -> None:
        adapter = Feishu(
            b.org_id, {"app_id": "synthetic-app", "app_secret": "synthetic"}
        )

        async def request(_method: str, _path: str, body: dict | None = None) -> dict:
            assert body is None or isinstance(body, dict)
            return {"data": {"chat_mode": "p2p", "message_id": uuid4().hex}}

        monkeypatch.setattr(adapter, "_request", request)

        async def send(user: str, token: str | None = None, message: str = "") -> None:
            event = (
                {
                    "operator": {"open_id": user},
                    "context": {"open_chat_id": "chat", "open_message_id": message},
                    "action": {"value": {"token": token}},
                }
                if token
                else {
                    "sender": {"sender_type": "user", "sender_id": {"open_id": user}},
                    "message": {
                        "message_type": "text",
                        "chat_type": "p2p",
                        "chat_id": "chat",
                        "content": json.dumps({"text": "申请延期"}),
                    },
                }
            )
            incoming = await adapter._event(
                json.dumps(
                    {
                        "header": {
                            "event_type": "card.action.trigger"
                            if token
                            else "im.message.receive_v1",
                            "event_id": uuid4().hex,
                        },
                        "event": event,
                    }
                )
            )
            assert incoming
            await gateway.Gateway(engine).handle(
                incoming, "synthetic-app", setting.credentials, adapter
            )

        try:
            await send("b")
            button = db.exec(
                select(ImAction).where(ImAction.org_id == b.org_id)
            ).first()
            assert button
            for user, message in (("a", button.message_id), ("b", "forged-message")):
                await send(user, button.token, message)
                assert not db.exec(
                    select(CapacityExtensionRequest).where(
                        CapacityExtensionRequest.org_id == b.org_id
                    )
                ).all()
            await send("b", button.token, button.message_id)
        finally:
            await adapter.close()

    asyncio.run(exercise())
    event = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == b.org_id,
            AuditEvent.action == "capacity.extension_requested",
        )
    ).one()
    assert event.details["via"] == "feishu"
    assert (
        db.exec(
            select(CapacityExtensionRequest).where(
                CapacityExtensionRequest.org_id == b.org_id
            )
        )
        .one()
        .assignment_id
        == row.id
    )
