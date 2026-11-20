"""A bot session must not reuse privileged history after its scope shrinks."""

from dataclasses import replace
from datetime import timedelta
from threading import Event
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.core.security import create_access_token
from app.domains.bot import core, store
from app.domains.bot import schemas as s
from app.domains.capacity.models import CapacitySetting
from app.domains.model.client import ModelUnavailable
from app.domains.model.pi_client import TurnResult, TurnStream
from app.domains.org.models import OrgMembership, Role, RoleGrant
from tests.domains.test_bot_core import enable
from tests.domains.test_im_gateway import principal

pytest_plugins = ("tests.api.routes.test_m8_coordination_tools",)

HIDDEN = "你的权限已变化，这条之前的回复已隐藏，请重新提问"
PRIVATE = "合成 B 部门姓名与个人负荷"


def _headers(db: Session, org_id: UUID, employee_id: UUID) -> dict[str, str]:
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.employee_id == employee_id,
        )
    ).one()
    return {
        "Authorization": f"Bearer {create_access_token(member.user_id, expires_delta=timedelta(minutes=10))}"
    }


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("change", ["revoke", "move", "disable"])
def test_scope_shrink_hides_history_and_resets_state(
    coordination: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    channel: str,
    change: str,
) -> None:
    people, _, demand = coordination
    p = principal(db, demand.org_id, people["E05"].id)
    enable(db, p.org_id)
    row = store.create_session(db, p, channel=channel)
    states: list[object] = []
    turn_number = 0
    private_text = f"{people['E04'].display_name} 的个人负荷为合成 80%"

    def fake_turn(_config: object, **kwargs: object) -> TurnResult:
        nonlocal turn_number
        states.append(kwargs["session_state"])
        turn_number += 1
        if turn_number == 2:
            db.refresh(row)
            assert row.state_ciphertext is None
        if turn_number == 1:
            call = kwargs["on_tool_call"]
            for function in ("simulate_plan", "inspect_capacity"):
                result = call(
                    "coordination",
                    {"function": function, "arguments": {"demand_id": str(demand.id)}},
                )
                assert "error" not in result
            assert "error" not in call(
                "get_demand_basis", {"demand_id": str(demand.id)}
            )
            assert "error" not in call("list_team_members", {})
        return TurnResult(
            text=private_text if turn_number == 1 else "新回复",
            stop_reason="stop",
            model_calls=1,
            tool_calls=4 if turn_number == 1 else 0,
            session_state={"version": 1, "messages": [{"private": private_text}]},
        )

    monkeypatch.setattr(core.pi_client, "run_turn", fake_turn)
    core.turn(db, p, row.id, "请查看部门方案")
    db.refresh(row)
    assert row.state_ciphertext is not None

    if change == "disable":
        setting = db.exec(
            select(CapacitySetting).where(CapacitySetting.org_id == p.org_id)
        ).one()
        setting.enabled = False
        db.add(setting)
    else:
        member = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == p.org_id,
                OrgMembership.employee_id == p.employee_id,
            )
        ).one()
        db.exec(
            delete(RoleGrant).where(
                RoleGrant.org_id == p.org_id,
                RoleGrant.membership_id == member.id,
                RoleGrant.role == Role.DEPT_MANAGER,
            )
        )
        if change == "move":
            db.add(
                RoleGrant(
                    org_id=p.org_id,
                    membership_id=member.id,
                    role=Role.DEPT_MANAGER,
                    department_id=people["E01"].department_id,
                )
            )
    db.commit()
    current = principal(db, p.org_id, people["E05"].id)
    if channel == "web":
        response = client.get(
            f"/api/v1/bot/sessions/{row.id}/messages",
            params={"org_id": str(p.org_id)},
            headers=_headers(db, p.org_id, people["E05"].id),
        )
        assert response.status_code == 200, response.text
        history = response.json()
    else:
        history = [
            message.model_dump(mode="json")
            for message in store.list_messages(db, current, row.id)
        ]
    assert history[0]["role"] == "user"
    assert history[0]["payload"]["text"] == "请查看部门方案"
    assert history[1]["payload"]["messages"] == [{"type": "text", "text": HIDDEN}]
    assert private_text not in str(history)
    assert people["E04"].display_name not in str(history)
    core.turn(db, current, row.id, "继续")
    assert states == [None, None]
    db.refresh(row)
    assert row.state_ciphertext is not None  # New turn starts a fresh state.


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("change", ["same", "expand", "legacy_safe"])
def test_scope_kept_when_not_narrowed(
    coordination: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    channel: str,
    change: str,
) -> None:
    people, _, demand = coordination
    p = principal(db, demand.org_id, people["E05"].id)
    enable(db, p.org_id)
    row = store.create_session(db, p, channel=channel)
    states: list[object] = []

    def fake_turn(_config: object, **kwargs: object) -> TurnResult:
        states.append(kwargs["session_state"])
        return TurnResult(
            text="合成普通回复",
            stop_reason="stop",
            model_calls=1,
            tool_calls=0,
            session_state={"version": 1, "messages": ["synthetic-safe"]},
        )

    monkeypatch.setattr(core.pi_client, "run_turn", fake_turn)
    if change == "legacy_safe":
        store.append_message(db, row, "user", {"text": "旧用户消息"})
        store.append_message(
            db, row, "assistant", {"messages": [{"type": "text", "text": "旧安全回复"}]}
        )
        store.save_state(db, row, {"version": 1, "messages": ["synthetic-safe"]})
        db.commit()
    else:
        core.turn(db, p, row.id, "你好")
    current = (
        replace(
            p,
            is_org_admin=True,
            managed_department_ids=p.managed_department_ids | {uuid4()},
        )
        if change == "expand"
        else p
    )
    assert any(
        message.role == "assistant" and "回复" in str(message.payload)
        for message in store.list_messages(db, current, row.id)
    )
    core.turn(db, current, row.id, "继续")
    assert states[-1] == {"version": 1, "messages": ["synthetic-safe"]}


def test_legacy_sensitive_session_is_treated_as_narrowed(
    coordination: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    people, _, demand = coordination
    p = principal(db, demand.org_id, people["E05"].id)
    enable(db, p.org_id)
    row = store.create_session(db, p)
    store.append_message(db, row, "user", {"text": "旧问题"})
    store.append_message(
        db,
        row,
        "assistant",
        {
            "messages": [{"type": "text", "text": PRIVATE}],
            "steps": [{"name": "查看团队成员", "ok": True}],
        },
    )
    store.save_state(
        db,
        row,
        {"version": 1, "messages": [{"name": "list_team_members", "private": PRIVATE}]},
    )
    db.commit()
    history = store.list_messages(db, p, row.id)
    assert history[0].payload == {"text": "旧问题"}
    assert history[1].payload == {"messages": [{"type": "text", "text": HIDDEN}]}
    seen: list[object] = []

    def fake_turn(_config: object, **kwargs: object) -> TurnResult:
        seen.append(kwargs["session_state"])
        return TurnResult(
            text="新回复", stop_reason="stop", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(core.pi_client, "run_turn", fake_turn)
    core.turn(db, p, row.id, "继续")
    assert seen == [None]


def test_pending_card_uses_existing_confirmation_check_after_shrink(
    coordination: tuple, db: Session
) -> None:
    people, _, demand = coordination
    p = principal(db, demand.org_id, people["E05"].id)
    row = store.create_session(db, p)
    card = s.CapacityCard(
        kind="workload_request",
        title="负荷调整",
        content="合成确认内容",
        arguments={},
    )
    action = s.CapacityAction(action="confirm_capacity", card_id=card.card_id)
    card.actions = [action]
    store.append_message(
        db,
        row,
        "assistant",
        {
            "messages": [card.model_dump(mode="json")],
            "scope": store.session_scope(db, p),
        },
    )
    setting = db.exec(
        select(CapacitySetting).where(CapacitySetting.org_id == p.org_id)
    ).one()
    setting.enabled = False
    db.commit()
    result = core.act(db, p, row.id, action)
    assert isinstance(result.messages[0], s.CapacityCard)
    assert result.messages[0].status == "failed"
    assert result.messages[0].result == "规划与负荷功能未开启"


def test_org_admin_revocation_also_hides_prior_reply(
    coordination: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    people, _, demand = coordination
    current = principal(db, demand.org_id, people["E05"].id)
    old = replace(current, is_org_admin=True)
    row = store.create_session(db, old)
    store.append_message(
        db,
        row,
        "assistant",
        {
            "messages": [{"type": "text", "text": PRIVATE}],
            "scope": store.session_scope(db, old),
        },
    )
    store.save_scoped_state(
        db, old, row, {"version": 1, "messages": [{"private": PRIVATE}]}
    )
    db.commit()
    assert store.list_messages(db, current, row.id)[0].payload == {
        "messages": [{"type": "text", "text": HIDDEN}]
    }
    enable(db, current.org_id)

    def fail_turn(_config: object, **kwargs: object) -> TurnResult:
        assert kwargs["session_state"] is None
        raise ModelUnavailable()

    monkeypatch.setattr(core.pi_client, "run_turn", fail_turn)
    core.turn(db, current, row.id, "继续")
    db.refresh(row)
    assert row.state_ciphertext is None


def test_scope_reset_does_not_commit_cancelled_stream_message(
    coordination: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    people, _, demand = coordination
    p = principal(db, demand.org_id, people["E05"].id)
    old = replace(p, is_org_admin=True)
    enable(db, p.org_id)
    row = store.create_session(db, p)
    store.save_scoped_state(db, old, row, {"version": 1, "messages": []})
    db.commit()
    stream = TurnStream(
        run_id=str(uuid4()),
        on_text=lambda _text: None,
        on_tool=lambda *_args: None,
        cancelled=Event(),
    )

    def cancel_turn(_config: object, **kwargs: object) -> TurnResult:
        assert kwargs["session_state"] is None
        stream.cancelled.set()
        return TurnResult(text="取消", stop_reason="stop", model_calls=1, tool_calls=0)

    monkeypatch.setattr(core.pi_client, "run_turn", cancel_turn)
    assert core.turn(db, p, row.id, "取消这一轮", stream=stream).messages == []
    db.refresh(row)
    assert row.state_ciphertext is None
    assert all(
        message.payload.get("text") != "取消这一轮"
        for message in store.messages_for(db, row)
        if isinstance(message.payload, dict)
    )
