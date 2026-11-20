import asyncio
import json
from collections.abc import Generator
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app.authz.principal import Principal
from app.core.db import engine
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.bot import core, store, tools
from app.domains.bot import schemas as s
from app.domains.capacity import assistant, demand
from app.domains.capacity.models import (
    CapacityDemand,
    CapacityDemandEvent,
    CapacityProject,
    CapacitySetting,
)
from app.domains.im import binding, gateway
from app.domains.im.adapters.feishu import Feishu
from app.domains.im.models import ImAction, ImSetting
from app.domains.org.models import Department, OrgMembership, RoleGrant
from app.domains.profile.models import ProfileVersion
from app.models import User
from tests.domains.test_bot_core import enable
from tests.domains.test_im_gateway import principal
from tests.utils.bot import final_response
from tests.utils.pi import PiHarness

pytestmark = pytest.mark.usefixtures("reset_login_attempts")


@pytest.fixture
def case(demo: tuple, db: Session, client: TestClient) -> Generator[tuple]:
    url, people, _ = demo
    a = principal(db, people["E02"].org_id, people["E02"].id)
    outsider = User(
        username=str(uuid4()), email=f"{uuid4()}@example.com", hashed_password="unused"
    )
    db.add(outsider)
    db.flush()
    db.add(
        OrgMembership(
            org_id=a.org_id, user_id=outsider.id, employee_id=people["E03"].id
        )
    )
    target = Department(org_id=a.org_id, name="合成承接部门")
    db.add(target)
    db.flush()
    for code in ("E04", "E05"):
        people[code].department_id = target.id
        people[code].display_name = f"合成承接{code}"
        db.add(people[code])
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
    setting = CapacitySetting(
        org_id=a.org_id, enabled=True, updated_by_user_id=a.user_id
    )
    db.add(setting)
    dictionary = client.get(f"{url}/capabilities", headers=demo[2]["admin"]).json()
    capability = dictionary["data"][0]
    for code in ("E04", "E05"):
        db.add(
            ProfileVersion(
                org_id=a.org_id,
                subject_employee_id=people[code].id,
                revision=99,
                published_at=utc_now(),
                schema_version_id=dictionary["schema_version"]["id"],
                published_by={"actor_type": "system_worker"},
                snapshot={
                    "claims": [
                        {
                            "id": str(uuid4()),
                            "capability_definition_id": capability["id"],
                            "statement": "PRIVATE_PROFILE",
                            "level": "routine",
                            "assessment_status": "confirmed",
                            "confidence": None,
                            "explanation": None,
                            "generated_by": {"actor_type": "user"},
                            "evidence": [],
                        }
                    ]
                },
            )
        )
    db.commit()
    b = principal(db, a.org_id, people["E05"].id)
    args = {
        "requesting_department_id": str(people["E02"].department_id),
        "target_department_id": str(target.id),
        "capability_key": capability["key"],
        "minimum_level": "routine",
        "headcount": 2,
        "allocation_percent": 50,
        "window_start": "2026-12-01",
        "window_end": "2026-12-31",
        "description": "合成需求",
    }
    yield a, b, args, setting
    db.rollback()
    for model in (
        CapacityDemandEvent,
        CapacityDemand,
        CapacityProject,
        CapacitySetting,
    ):
        db.exec(delete(model).where(model.org_id == a.org_id))
    db.commit()


def prepare(
    db: Session, p: Principal, args: dict, name: str = "prepare_demand"
) -> tuple:
    session = store.create_session(db, p)
    cards = []
    result = tools.call(db, p, session, cards, name, args)
    assert not isinstance(result, dict) or "error" not in result, result
    assert len(cards) == 1
    store.append_message(
        db, session, "assistant", s.TurnResponse(messages=cards).model_dump(mode="json")
    )
    db.commit()
    return session, cards[0]


def assert_private_absent(value: Any, demo: tuple) -> None:
    raw = json.dumps(value, ensure_ascii=False, default=str)
    assert "PRIVATE_PROFILE" not in raw
    for code in ("E04", "E05"):
        person = demo[1][code]
        for private in (str(person.id), person.display_name, person.employee_code):
            assert private not in raw


def test_pi_web_reference_and_confirm(
    case: tuple,
    demo: tuple,
    db: Session,
    client: TestClient,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, _, args, _ = case
    enable(db, a.org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    pi_service.replies = [
        {
            "calls": [
                {
                    "name": "capacity_reference",
                    "arguments": {
                        k: args[k]
                        for k in (
                            "target_department_id",
                            "capability_key",
                            "minimum_level",
                        )
                    },
                }
            ]
        },
        {"calls": [{"name": "prepare_demand", "arguments": args}]},
        "请核对并点击确认",
    ]
    base = "/api/v1/bot/sessions"
    query = {"org_id": str(a.org_id)}
    headers = demo[2]["employee"]
    created = client.post(base, params=query, headers=headers)
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]
    response = final_response(
        client.post(
            f"{base}/{session_id}/turns",
            params=query,
            headers=headers,
            json={"text": "我已确认，请帮我问参考并提交需求"},
        )
    )
    card = next(m for m in response["messages"] if m["type"] == "capacity_card")
    assert card["status"] == "pending" and "参考不是承诺" in card["content"]
    assert "routine" not in card["content"]
    assert not db.exec(
        select(CapacityDemand).where(CapacityDemand.org_id == a.org_id)
    ).all()
    assert_private_absent(response, demo)
    assert_private_absent(pi_service.requests, demo)
    confirmed = client.post(
        f"{base}/{session_id}/actions",
        params=query,
        headers=headers,
        json=card["actions"][0],
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["messages"][0]["status"] == "confirmed"
    assert (
        client.post(
            f"{base}/{session_id}/actions",
            params=query,
            headers=headers,
            json=card["actions"][0],
        ).status_code
        == 409
    )
    event = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == a.org_id,
            AuditEvent.action == "capacity.demand_submitted",
        )
    ).one()
    assert event.details == {"via": "bot", "client_name": "web"}


def test_read_results_scope_and_definitions(
    case: tuple, demo: tuple, db: Session
) -> None:
    a, b, args, setting = case
    session, card = prepare(db, a, args)
    core.act(db, a, session.id, card.actions[0])
    row = db.exec(select(CapacityDemand).where(CapacityDemand.org_id == a.org_id)).one()
    project = CapacityProject(
        org_id=a.org_id,
        requesting_department_id=row.requesting_department_id,
        requester_user_id=row.requester_user_id,
        title="合成项目",
        description="合成项目说明",
    )
    db.add(project)
    db.flush()
    row.project_id, row.role_name = project.id, "合成角色"
    db.add(row)
    db.commit()

    def read(name: str, body: dict[str, Any]) -> Any:
        if name in ("list_projects", "get_project"):
            return tools.call(
                db,
                a,
                session,
                [],
                "coordination",
                {"function": name, "arguments": body},
            )
        return tools.call(db, a, session, [], name, body)

    for name in assistant.READS:
        body = (
            {"demand_id": str(row.id)}
            if name in ("get_demand", "get_demand_basis")
            else {
                k: args[k]
                for k in ("target_department_id", "capability_key", "minimum_level")
            }
            if name == "capacity_reference"
            else {"project_id": str(project.id)}
            if name == "get_project"
            else {}
        )
        result = read(name, body)
        assert_private_absent(result, demo)
        assert (isinstance(result, dict) and "error" in result) == (
            name == "get_demand_basis"
        )
    assert (
        tools.call(db, b, session, [], "list_demands", {"side": "incoming"})["count"]
        == 1
    )
    outsider = principal(db, a.org_id, demo[1]["E03"].id)
    assert tools.call(db, outsider, session, [], "list_demands", {})["count"] == 0

    def names(p: Principal) -> set[str]:
        definitions = tools.definitions(p, db=db)
        assert len(definitions) <= 29
        return {t.name for t in definitions}

    assert "prepare_demand" in names(a) and "get_demand_basis" not in names(a)
    setting.requester_policy = "managers"
    db.add(setting)
    db.commit()
    assert "prepare_demand" not in names(a) and "prepare_demand" in names(b)
    cards = []
    assert "error" in tools.call(db, a, session, cards, "prepare_demand", args)
    assert not cards
    setting.enabled = False
    db.add(setting)
    db.commit()
    assert not (set(assistant.TITLES) & names(b))
    for name in (*assistant.TITLES, *assistant.WRITES):
        assert tools.call(db, b, session, [], name, {}) == {
            "error": "请通过 coordination 调用此工具"
            if name in ("list_projects", "get_project")
            else "规划与负荷功能未开启"
        }


@pytest.mark.parametrize(
    "attack", ["org_id", "employee_id", "user_id", "via", "department"]
)
def test_forged_prepare_has_no_card(case: tuple, db: Session, attack: str) -> None:
    a, b, args, _ = case
    payload = {**args, attack: str(uuid4())}
    if attack == "department":
        payload = {
            **args,
            "requesting_department_id": str(next(iter(b.managed_department_ids))),
        }
        payload["target_department_id"] = args["requesting_department_id"]
    cards = []
    assert "error" in tools.call(
        db, a, store.create_session(db, a), cards, "prepare_demand", payload
    )
    assert not cards


@pytest.mark.parametrize("action", ["accept", "follow_up", "withdraw"])
def test_response_confirmation_and_forgery(
    case: tuple, demo: tuple, db: Session, client: TestClient, action: str
) -> None:
    a, b, args, _ = case
    session, card = prepare(db, a, args)
    core.act(db, a, session.id, card.actions[0])
    row = db.exec(select(CapacityDemand).where(CapacityDemand.org_id == a.org_id)).one()
    demand.reply_demand(
        b,
        db,
        row.id,
        "can_do",
        "部门可以承接",
        1,
        start_date=row.window_start,
        end_date=row.window_end,
    )
    db.commit()
    body = {
        "demand_id": str(row.id),
        "action": action,
        "revision": 2,
        "text": "合成回应",
    }
    for name, payload in (
        ("get_demand", {"demand_id": str(row.id)}),
        ("list_demands", {}),
    ):
        assert_private_absent(tools.call(db, a, session, [], name, payload), demo)
    for p in (
        b,
        principal(db, a.org_id, demo[1]["E03"].id),
        replace(a, org_id=uuid4()),
    ):
        cards = []
        assert "error" in tools.call(
            db, p, session, cards, "prepare_demand_response", body
        )
        assert not cards
    response_session, response = prepare(db, a, body, "prepare_demand_response")
    assert_private_absent(response.model_dump(mode="json"), demo)
    path = f"/api/v1/bot/sessions/{response_session.id}/actions"
    query = {"org_id": str(a.org_id)}
    offered = response.actions[0].model_dump(mode="json")
    for headers in (demo[2]["manager"], demo[2]["foreign"]):
        assert (
            client.post(path, params=query, headers=headers, json=offered).status_code
            == 404
        )
    assert (
        client.post(
            path,
            params=query,
            headers=demo[2]["employee"],
            json={**offered, "card_id": str(uuid4())},
        ).status_code
        == 409
    )
    assert (
        client.post(
            path,
            params=query,
            headers=demo[2]["employee"],
            json={**offered, "employee_id": str(b.employee_id)},
        ).status_code
        == 422
    )
    result = core.act(db, a, response_session.id, response.actions[0]).messages[0]
    assert result.status == "confirmed"
    assert (
        demand.get_demand(a, db, row.id).status
        == {"accept": "accepted", "follow_up": "submitted", "withdraw": "withdrawn"}[
            action
        ]
    )


@pytest.mark.parametrize("change", ["cancel", "disabled", "scope", "stale"])
def test_cancel_and_current_state_rechecked(
    case: tuple, demo: tuple, db: Session, change: str
) -> None:
    a, _, args, setting = case
    session, card = prepare(db, a, args)
    if change == "cancel":
        result = core.act(db, a, session.id, card.actions[1]).messages[0]
        assert result.status == "cancelled"
    elif change == "stale":
        core.act(db, a, session.id, card.actions[0])
        row = db.exec(
            select(CapacityDemand).where(CapacityDemand.org_id == a.org_id)
        ).one()
        session, card = prepare(
            db,
            a,
            {"demand_id": str(row.id), "action": "withdraw", "revision": 1},
            "prepare_demand_response",
        )
        demand.respond_demand(a, db, row.id, "withdraw", 1)
        db.commit()
        assert (
            core.act(db, a, session.id, card.actions[0]).messages[0].status == "failed"
        )
    else:
        if change == "disabled":
            setting.enabled = False
            db.add(setting)
        else:
            demo[1]["E02"].department_id = demo[1]["E04"].department_id
            db.add(demo[1]["E02"])
        db.commit()
        assert (
            core.act(db, a, session.id, card.actions[0]).messages[0].status == "failed"
        )
    with pytest.raises(demand.CapacityConflict):
        core.act(db, a, session.id, card.actions[0])


def test_feishu_bound_buttons_and_audit(
    case: tuple,
    demo: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    pi_service: PiHarness,
) -> None:
    a, b, args, _ = case
    enable(db, a.org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    pi_service.replies = [
        {"calls": [{"name": "prepare_demand", "arguments": args}]},
        "请核对",
    ]
    setting = ImSetting(
        org_id=a.org_id,
        platform="feishu",
        enabled=True,
        credentials="synthetic-encrypted",
        bot_identity="synthetic-app",
        updated_by_user_id=a.user_id,
        email_auto_bind=False,
    )
    db.add(setting)
    db.commit()
    for p, user in ((a, "a"), (b, "b")):
        assert binding.bind(
            db,
            a.org_id,
            "feishu",
            user,
            "chat",
            "synthetic-app",
            binding.create_code(db, p).code,
        )
    outgoing = []

    async def exercise() -> None:
        adapter = Feishu(
            a.org_id, {"app_id": "synthetic-app", "app_secret": "synthetic"}
        )

        async def request(_method: str, _path: str, body: dict | None = None) -> dict:
            outgoing.append(body or {})
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
                        "content": json.dumps({"text": "请提交需求"}),
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
            await send("a")
            button = db.exec(
                select(ImAction).where(ImAction.org_id == a.org_id)
            ).first()
            assert button
            assert_private_absent(outgoing, demo)
            assert "确认" in json.dumps(
                outgoing, ensure_ascii=False
            ) and "取消" in json.dumps(outgoing, ensure_ascii=False)
            await send("b", button.token, button.message_id)
            assert not db.exec(
                select(CapacityDemand).where(CapacityDemand.org_id == a.org_id)
            ).all()
            await send("a", button.token, "forged-message")
            assert not db.exec(
                select(CapacityDemand).where(CapacityDemand.org_id == a.org_id)
            ).all()
            await send("a", button.token, button.message_id)
        finally:
            await adapter.close()

    asyncio.run(exercise())
    event = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == a.org_id,
            AuditEvent.action == "capacity.demand_submitted",
        )
    ).one()
    assert event.details == {"via": "feishu", "client_name": "feishu"}
