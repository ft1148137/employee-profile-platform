"""Session privacy follows server-reported reads, including inherited context."""

import json
from dataclasses import replace
from typing import Literal

import pytest
from sqlmodel import Session, select

from app.core.secrets import encrypt_secret
from app.domains.bot import core, delivery, store
from app.domains.bot.models import BotMessage
from app.domains.capacity.models import (
    CapacityDemandEvent,
    CapacitySetting,
    DemandEventKind,
)
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import (
    Employee,
    EmploymentStatus,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.planning.tools import Result
from tests.api.routes.test_m8_coordination_tools import assert_no_b_people, invoke
from tests.api.routes.test_m8_employee_session_visibility import (
    demo as demo,  # noqa: F401
)
from tests.domains.test_im_gateway import principal

pytest_plugins = ("tests.api.routes.test_m8_coordination_tools",)


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_three_argument_handler_without_read_metadata_keeps_fallback(
    coordination: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu"],
) -> None:
    people, _, demand = coordination
    p = principal(db, demand.org_id, people["E01"].id)
    row = store.create_session(db, p, channel=channel)
    tool = core.tools.COORDINATION["get_approval_status"]

    def unreported(viewer: object, session: Session, body: object) -> Result:
        return Result(tool.handler(viewer, session, body).model_dump(mode="json"))

    monkeypatch.setitem(
        core.tools.COORDINATION, tool.name, replace(tool, handler=unreported)
    )

    def model(_config: object, **kwargs: object) -> TurnResult:
        result = kwargs["on_tool_call"](
            "coordination",
            {
                "function": tool.name,
                "arguments": {"demand_id": str(demand.id)},
            },
        )
        assert "error" not in result
        assert "read_employee_ids" not in json.dumps(result)
        return TurnResult(
            text="兼容旧工具",
            stop_reason="stop",
            model_calls=1,
            tool_calls=1,
            session_state={"messages": ["合成上下文"]},
        )

    monkeypatch.setattr(core.pi_client, "run_turn", model)
    pending = core.turn(db, p, row.id, "查看进度")
    target = people["E02"]
    assert target.department_id in p.managed_department_ids
    target.employment_status = EmploymentStatus.INACTIVE
    db.add(target)
    db.commit()
    assert store.HIDDEN_REPLY in str(store.list_messages(db, p, row.id))
    assert delivery.protect(db, pending).messages[0].text == delivery.SCOPE_CHANGED
    assert store.state_for_turn(db, p, row) is None


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("change", ["transfer", "offboard"])
@pytest.mark.parametrize("source", ["tracked", "unreported", "legacy"])
@pytest.mark.parametrize("read_target", [False, True])
def test_employee_change_checks_only_reads_with_conservative_fallback(
    coordination: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu"],
    change: str,
    source: str,
    read_target: bool,
) -> None:
    people, _, demand = coordination
    p = principal(db, demand.org_id, people["E05"].id)
    row = store.create_session(db, p, channel=channel)
    states: list[object] = []
    original_call = core.tools.call

    def unreported(*args: object, **kwargs: object) -> object:
        kwargs.pop("on_read", None)
        return original_call(*args, **kwargs)

    if source == "unreported":
        monkeypatch.setattr(core.tools, "call", unreported)

    def model(_config: object, **kwargs: object) -> TurnResult:
        states.append(kwargs["session_state"])
        if len(states) == 1:
            result = kwargs["on_tool_call"](
                "get_member_profile", {"employee_id": str(people["E04"].id)}
            )
            assert "error" not in result
        return TurnResult(
            text="已读取甲的画像",
            stop_reason="stop",
            model_calls=1,
            tool_calls=1 if len(states) == 1 else 0,
            session_state={"messages": ["已读取甲的画像"]},
        )

    monkeypatch.setattr(core.pi_client, "run_turn", model)
    pending = core.turn(db, p, row.id, "只看甲")
    if source == "legacy":
        scope = store.session_scope(db, p)
        message = store.messages_for(db, row)[-1]
        payload = message.payload
        payload["scope"] = scope
        stored = db.get_one(BotMessage, message.id)
        stored.ciphertext = encrypt_secret(json.dumps(payload, ensure_ascii=False))
        db.add(stored)
        store.save_scoped_state(
            db, p, row, {"messages": ["已读取甲的画像"]}, scope=scope
        )
        db.commit()
        pending._scope = scope
    else:
        # A no-tool turn must retain the employees in the inherited Pi context.
        pending = core.turn(db, p, row.id, "沿用旧上下文")
    target = people["E04"] if read_target else people["E06"]
    assert target.department_id in p.managed_department_ids
    if change == "transfer":
        target.department_id = people["E10"].department_id
    else:
        target.employment_status = EmploymentStatus.INACTIVE
    db.add(target)
    db.commit()
    hidden = read_target or source != "tracked"
    history = str(store.list_messages(db, p, row.id))
    if hidden:
        assert "已读取甲的画像" not in history
        assert store.HIDDEN_REPLY in history
        assert delivery.protect(db, pending).messages[0].text == delivery.SCOPE_CHANGED
        assert store.state_for_turn(db, p, row) is None
        db.refresh(row)
        assert row.state_ciphertext is None
    else:
        assert "已读取甲的画像" in history
        assert delivery.protect(db, pending).messages == pending.messages
        assert store.state_for_turn(db, p, row) == {"messages": ["已读取甲的画像"]}


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_admin_stores_only_two_server_reported_employee_ids(
    coordination: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu"],
) -> None:
    people, _, demand = coordination
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == demand.org_id,
            OrgMembership.employee_id == people["E05"].id,
        )
    ).one()
    db.add(
        RoleGrant(org_id=demand.org_id, membership_id=member.id, role=Role.ORG_ADMIN)
    )
    db.commit()
    p = principal(db, demand.org_id, people["E05"].id)
    assert p.is_org_admin
    row = store.create_session(db, p, channel=channel)
    expected = {str(people[code].id) for code in ("E04", "E06")}

    def model(_config: object, **kwargs: object) -> TurnResult:
        for id in expected:
            assert "error" not in kwargs["on_tool_call"](
                "get_member_profile", {"employee_id": id}
            )
        return TurnResult(
            text="只读两人",
            stop_reason="stop",
            model_calls=1,
            tool_calls=2,
            session_state={"messages": []},
        )

    monkeypatch.setattr(core.pi_client, "run_turn", model)
    core.turn(db, p, row.id, "只读两人")
    payload = store.messages_for(db, row)[-1].payload
    saved = store.load_state(db, row, scoped=True)
    for scope in (payload["scope"], saved["scope"]):
        assert set(scope["read_employee_ids"]) == expected
        assert "visible_employee_ids" not in scope


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("change", ["revoke", "move", "disable", "admin_revoke"])
def test_metadata_shrink_still_hides_even_without_employee_reads(
    coordination: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu"],
    change: str,
) -> None:
    people, _, demand = coordination
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == demand.org_id,
            OrgMembership.employee_id == people["E05"].id,
        )
    ).one()
    if change == "admin_revoke":
        db.add(
            RoleGrant(
                org_id=demand.org_id, membership_id=member.id, role=Role.ORG_ADMIN
            )
        )
        db.commit()
    p = principal(db, demand.org_id, people["E05"].id)
    row = store.create_session(db, p, channel=channel)

    def model(_config: object, **_kwargs: object) -> TurnResult:
        return TurnResult(
            text="受范围保护的回复",
            stop_reason="stop",
            model_calls=1,
            tool_calls=0,
            session_state={"messages": ["合成上下文"]},
        )

    monkeypatch.setattr(core.pi_client, "run_turn", model)
    pending = core.turn(db, p, row.id, "你好")
    assert pending._scope["read_employee_ids"] == []
    if change == "disable":
        setting = db.exec(
            select(CapacitySetting).where(CapacitySetting.org_id == p.org_id)
        ).one()
        setting.enabled = False
        db.add(setting)
    else:
        role = Role.ORG_ADMIN if change == "admin_revoke" else Role.DEPT_MANAGER
        for grant in db.exec(
            select(RoleGrant).where(
                RoleGrant.org_id == p.org_id,
                RoleGrant.membership_id == member.id,
                RoleGrant.role == role,
            )
        ).all():
            db.delete(grant)
        if change == "move":
            db.add(
                RoleGrant(
                    org_id=p.org_id,
                    membership_id=member.id,
                    role=Role.DEPT_MANAGER,
                    department_id=people["E10"].department_id,
                )
            )
    db.commit()
    current = principal(db, p.org_id, p.employee_id)
    assert store.HIDDEN_REPLY in str(store.list_messages(db, current, row.id))
    assert "受范围保护的回复" not in str(store.list_messages(db, current, row.id))
    assert delivery.protect(db, pending).messages[0].text == delivery.SCOPE_CHANGED
    assert store.state_for_turn(db, current, row) is None
    assert row.state_ciphertext is None


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("change", ["transfer", "offboard"])
def test_unread_employee_change_during_turn_keeps_final_reply(
    coordination: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu"],
    change: str,
) -> None:
    people, _, demand = coordination
    p = principal(db, demand.org_id, people["E05"].id)
    row = store.create_session(db, p, channel=channel)

    def model(_config: object, **kwargs: object) -> TurnResult:
        assert "error" not in kwargs["on_tool_call"](
            "get_member_profile",
            {
                "employee_id": str(people["E04"].id),
            },
        )
        with Session(db.get_bind()) as other:
            target = other.get_one(Employee, people["E06"].id)
            if change == "transfer":
                target.department_id = people["E10"].department_id
            else:
                target.employment_status = EmploymentStatus.INACTIVE
            other.add(target)
            other.commit()
        return TurnResult(
            text="甲的安全回复",
            stop_reason="stop",
            model_calls=1,
            tool_calls=1,
            session_state={"messages": ["安全上下文"]},
        )

    monkeypatch.setattr(core.pi_client, "run_turn", model)
    pending = core.turn(db, p, row.id, "只看甲")
    assert pending.messages[0].text == "甲的安全回复"
    with delivery.guard(db, pending) as protected:
        assert protected.messages == pending.messages
    assert "甲的安全回复" in str(store.list_messages(db, p, row.id))
    assert store.state_for_turn(db, p, row) == {"messages": ["安全上下文"]}


@pytest.mark.parametrize("tool", ["read_coordination_context", "get_approval_status"])
def test_a_side_gets_hidden_reply_notice_and_details_link(
    coordination: tuple, db: Session, tool: str
) -> None:
    people, _, demand = coordination
    a = principal(db, demand.org_id, people["E01"].id)
    b = principal(db, demand.org_id, people["E05"].id)
    private = f"只有{people['E04'].display_name}能参加的合成条件"
    db.add(
        CapacityDemandEvent(
            org_id=demand.org_id,
            demand_id=demand.id,
            kind=DemandEventKind.REPLIED,
            actor_user_id=b.user_id,
            via="web",
            text=private,
        )
    )
    db.commit()
    result = invoke(tool, a, db, demand_id=demand.id)
    detail = result["demand"] if tool == "read_coordination_context" else result
    assert detail["reply_notice"] == "没有读取对方答复正文里的条件，请到需求详情查看"
    assert detail["demand_url"] == f"/capacity?tab=outgoing&demand={demand.id}"
    assert private not in str(result)
    assert_no_b_people(result, people)
    result = invoke(tool, b, db, demand_id=demand.id)
    detail = result["demand"] if tool == "read_coordination_context" else result
    assert "reply_notice" not in detail
    assert "demand_url" not in detail
    assert detail["events"][0]["text"] == private
