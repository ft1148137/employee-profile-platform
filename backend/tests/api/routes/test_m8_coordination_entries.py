import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.audit.models import AuditEvent
from app.domains.bot import core, store, tools
from app.domains.bot import schemas as s
from app.domains.capacity.models import CapacitySetting
from app.domains.im.models import ImNotification
from app.domains.model.client import ModelUnavailable
from app.domains.model.pi_client import TurnResult
from app.domains.planning import service as planning_service
from app.domains.planning.models import PlanningCase
from app.domains.planning.tools import COORDINATION_TOOLS
from app.mcp.server import available_tools
from tests.api.routes.test_m7_planning_readonly import business_rows
from tests.api.routes.test_m8_coordination_tools import assert_no_b_people
from tests.api.routes.test_mcp import call as mcp_call
from tests.api.routes.test_mcp import initialize, token_for
from tests.domains.test_bot_core import enable
from tests.domains.test_im_gateway import principal

pytest_plugins = ("tests.api.routes.test_m8_coordination_tools",)

FORBIDDEN = {
    "create_assignment",
    "update_assignment",
    "update_assignment_due_date",
    "set_assignment_start_date",
    "approve_extension",
    "approve_workload_change",
    "decide_extension_request",
    "decide_workload_request",
    "set_week",
    "set_weeks",
    "reply_demand",
}


def test_definitions_and_forbidden_operations(coordination: tuple, db: Session) -> None:
    people, _, row = coordination
    b = principal(db, row.org_id, people["E05"].id)
    a = principal(db, row.org_id, people["E01"].id)
    maximum = replace(
        b,
        is_org_admin=True,
        managed_department_ids=frozenset(
            {people["E01"].department_id, people["E04"].department_id}
        ),
    )
    assert not ({tool.name for tool in COORDINATION_TOOLS} & FORBIDDEN)
    definitions = tools.definitions(maximum, db=db)
    names = {definition.name for definition in definitions}
    assert len(definitions) <= 29 and len(names) == len(definitions)
    assert {"coordination", "prepare_plan_proposal"} <= names
    assert not (names & FORBIDDEN)
    for channel in ("web", "feishu"):
        exposed = {tool.name for tool in tools.definitions(a, db=db, channel=channel)}
        assert "coordination" in exposed
        assert "reply_demand" not in exposed
        assert not (exposed & FORBIDDEN)
    mcp = available_tools(b, db)
    assert set(mcp) & FORBIDDEN == {
        "reply_demand"
    }  # Existing MCP direct-submit contract.
    assert "prepare_plan_proposal" not in mcp
    assert {"simulate_plan", "compare_plan_options"} <= set(mcp)


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_bot_privacy_and_proposal_card(
    coordination: tuple, db: Session, channel: str
) -> None:
    people, _, row = coordination
    a = principal(db, row.org_id, people["E01"].id)
    b = principal(db, row.org_id, people["E05"].id)
    a_session = store.create_session(db, a, channel=channel)
    a_cards: list[s.Message] = []
    for function in (
        "read_coordination_context",
        "inspect_capacity",
        "search_qualified_candidates",
        "get_approval_status",
    ):
        result = tools.call(
            db,
            a,
            a_session,
            a_cards,
            "coordination",
            {"function": function, "arguments": {"demand_id": str(row.id)}},
        )
        assert_no_b_people(result, people)
    assert tools.call(
        db,
        a,
        a_session,
        a_cards,
        "coordination",
        {"function": "simulate_plan", "arguments": {"demand_id": str(row.id)}},
    ) == {"error": "无权查看方案"}
    b_session = store.create_session(db, b, channel=channel)
    b_cards: list[s.Message] = []
    plan = tools.call(
        db,
        b,
        b_session,
        b_cards,
        "coordination",
        {"function": "simulate_plan", "arguments": {"demand_id": str(row.id)}},
    )
    assert plan["case_id"]
    choice = plan["options"][0]["id"]
    plan_count = len(b_cards)
    assert plan_count == len(plan["options"])
    assert all(isinstance(card, s.PlanCard) for card in b_cards)
    before = business_rows(db, row.org_id)
    rejected = tools.call(
        db,
        b,
        b_session,
        b_cards,
        "prepare_plan_proposal",
        {
            "case_id": plan["case_id"],
            "option_id": choice,
            "text": f"安排{people['E04'].display_name}",
        },
    )
    assert rejected == {"error": "答复只写部门层面的结论，不写人名"}
    assert len(b_cards) == plan_count
    assert business_rows(db, row.org_id) == before
    missing_reason = tools.call(
        db,
        b,
        b_session,
        b_cards,
        "prepare_plan_proposal",
        {
            "case_id": plan["case_id"],
            "option_id": choice,
            "text": "部门可在约定时间交付",
        },
    )
    assert "原因类型" in missing_reason["error"]
    assert len(b_cards) == plan_count
    assert business_rows(db, row.org_id) == before
    result = tools.call(
        db,
        b,
        b_session,
        b_cards,
        "prepare_demand_reply",
        {
            "demand_id": str(row.id),
            "revision": row.revision,
            "outcome": "not_now",
            "text": "目前无法承接",
            "reason_code": "skill",
        },
    )
    assert result["status"] == "pending"
    assert len(b_cards) == plan_count + 1 and isinstance(b_cards[-1], s.CapacityCard)
    assert b_cards[-1].kind == "demand_reply"
    assert_no_b_people(b_cards[-1].model_dump(mode="json"), people)
    assert business_rows(db, row.org_id) == before
    db.refresh(row)
    assert row.status == "submitted"
    assert tools.call(
        db,
        a,
        a_session,
        a_cards,
        "prepare_plan_proposal",
        {"case_id": plan["case_id"], "option_id": choice, "text": "部门可交付"},
    )["error"]
    assert not a_cards
    for code in ("E02", "E04", "E10"):
        other = principal(db, row.org_id, people[code].id)
        other_session = store.create_session(db, other, channel=channel)
        result = tools.call(
            db,
            other,
            other_session,
            [],
            "coordination",
            {
                "function": "read_coordination_context",
                "arguments": {"demand_id": str(row.id)},
            },
        )
        assert "error" in result
        assert_no_b_people(result, people)
    foreign = principal(db, people["foreign"].org_id, people["foreign"].id)
    foreign_session = store.create_session(db, foreign, channel=channel)
    assert "error" in tools.call(
        db,
        foreign,
        foreign_session,
        [],
        "coordination",
        {
            "function": "read_coordination_context",
            "arguments": {"demand_id": str(row.id)},
        },
    )
    store.append_message(
        db,
        b_session,
        "assistant",
        s.TurnResponse(messages=b_cards).model_dump(mode="json"),
    )
    db.commit()
    confirmed = core.act(db, b, b_session.id, b_cards[-1].actions[0])
    assert isinstance(confirmed.messages[0], s.CapacityCard)
    assert confirmed.messages[0].status == "confirmed"
    db.refresh(row)
    assert row.status == "replied"
    status = tools.call(
        db,
        a,
        a_session,
        [],
        "coordination",
        {"function": "get_approval_status", "arguments": {"demand_id": str(row.id)}},
    )
    assert status["status"] == "已答复"
    assert_no_b_people(status, people)
    setting = db.exec(
        select(CapacitySetting).where(CapacitySetting.org_id == row.org_id)
    ).one()
    setting.enabled = False
    db.commit()
    assert "error" in tools.call(
        db,
        b,
        b_session,
        [],
        "coordination",
        {"function": "inspect_capacity", "arguments": {"demand_id": str(row.id)}},
    )


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_complete_bot_loop(
    coordination: tuple, db: Session, monkeypatch: pytest.MonkeyPatch, channel: str
) -> None:
    people, _, row = coordination
    b = principal(db, row.org_id, people["E05"].id)
    enable(db, row.org_id)
    session = store.create_session(db, b, channel=channel)
    calls: list[str] = []

    def fake_turn(*_args: object, **kwargs: object) -> TurnResult:
        use = kwargs["on_tool_call"]

        def run(function: str, arguments: dict) -> dict:
            calls.append(function)
            result = use("coordination", {"function": function, "arguments": arguments})
            assert "error" not in result, result
            return result

        run("read_coordination_context", {"demand_id": str(row.id)})
        run("inspect_capacity", {"demand_id": str(row.id)})
        run("search_qualified_candidates", {"demand_id": str(row.id)})
        plan = run("simulate_plan", {"demand_id": str(row.id)})
        changed = run(
            "simulate_plan",
            {
                "demand_id": str(row.id),
                "assignees": [
                    {
                        "employee_id": str(people["E04"].id),
                        "allocation_percent": 50,
                        "start_date": row.window_start.isoformat(),
                        "end_date": row.window_end.isoformat(),
                    }
                ],
            },
        )
        run(
            "compare_plan_options",
            {
                "case_id": plan["case_id"],
                "option_ids": [plan["options"][0]["id"], changed["option"]["id"]],
            },
        )
        result = use(
            "prepare_plan_proposal",
            {
                "case_id": plan["case_id"],
                "option_id": changed["option"]["id"],
                "text": "部门可按方案交付",
            },
        )
        assert result["status"] == "pending"
        return TurnResult(
            text="请经理确认", stop_reason="stop", model_calls=1, tool_calls=7
        )

    monkeypatch.setattr(core.pi_client, "run_turn", fake_turn)
    response = core.turn(db, b, session.id, "请协调这项业务需求")
    assert calls == [
        "read_coordination_context",
        "inspect_capacity",
        "search_qualified_candidates",
        "simulate_plan",
        "simulate_plan",
        "compare_plan_options",
    ]
    assert any(
        isinstance(message, s.CapacityCard) and message.status == "pending"
        for message in response.messages
    )
    db.refresh(row)
    assert row.status == "submitted"


def test_tool_failure_rolls_back_partial_plan(
    coordination: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    people, _, row = coordination
    b = principal(db, row.org_id, people["E05"].id)
    session = store.create_session(db, b)

    def fail_after_insert(_p: object, transaction: Session, _demand_id: object) -> None:
        transaction.add(
            PlanningCase(
                org_id=row.org_id, demand_id=row.id, created_by_user_id=b.user_id
            )
        )
        transaction.flush()
        raise RuntimeError("private database detail")

    monkeypatch.setattr(planning_service, "create_case", fail_after_insert)
    with pytest.raises(ModelUnavailable):
        tools.call(
            db,
            b,
            session,
            [],
            "coordination",
            {"function": "simulate_plan", "arguments": {"demand_id": str(row.id)}},
        )
    assert (
        db.exec(select(PlanningCase).where(PlanningCase.org_id == row.org_id)).all()
        == []
    )


def test_model_failure_discards_plan(
    coordination: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    people, _, row = coordination
    b = principal(db, row.org_id, people["E05"].id)
    enable(db, row.org_id)
    session = store.create_session(db, b)

    def abort(*_args: object, **kwargs: object) -> TurnResult:
        result = kwargs["on_tool_call"](
            "coordination",
            {"function": "simulate_plan", "arguments": {"demand_id": str(row.id)}},
        )
        assert "case_id" in result
        return TurnResult(text="", stop_reason="aborted", model_calls=1, tool_calls=1)

    monkeypatch.setattr(core.pi_client, "run_turn", abort)
    response = core.turn(db, b, session.id, "请协调")
    assert any(
        "模型服务暂时不可用" in message.text
        for message in response.messages
        if isinstance(message, s.TextMessage)
    )
    assert (
        db.exec(select(PlanningCase).where(PlanningCase.org_id == row.org_id)).all()
        == []
    )


def test_mcp_scope_and_disabled(
    coordination: tuple, db: Session, client: TestClient
) -> None:
    people, _, row = coordination
    headers = {
        role: initialize(client, token_for(db, person)[0], name="Coordination client")
        for role, person in {
            "a": people["E01"],
            "b": people["E05"],
            "worker": people["E04"],
            "outside": people["E10"],
            "foreign": people["foreign"],
        }.items()
    }
    before = business_rows(db, row.org_id)

    def use(role: str, name: str, arguments: dict) -> dict:
        org_id = people["foreign"].org_id if role == "foreign" else row.org_id
        query = select(AuditEvent).where(
            AuditEvent.org_id == org_id, AuditEvent.action == "mcp.tool_called"
        )
        previous = {event.id for event in db.exec(query).all()}
        result = mcp_call(client, headers[role], name, arguments)
        added = [event for event in db.exec(query).all() if event.id not in previous]
        assert len(added) == 1
        assert added[0].details["client_name"] == "Coordination client"
        assert added[0].target_type == "mcp_tool"
        assert added[0].target_id == name
        return result

    notifications = select(ImNotification).where(ImNotification.org_id == row.org_id)
    before_notifications = [n.model_dump_json() for n in db.exec(notifications).all()]
    assert use("b", "inspect_capacity", {"demand_id": "invalid"}).get("isError")
    for name in (
        "read_coordination_context",
        "inspect_capacity",
        "search_qualified_candidates",
        "get_approval_status",
    ):
        result = use("a", name, {"demand_id": str(row.id)})
        assert not result.get("isError"), result
        assert_no_b_people(result["structuredContent"], people)
    plan = use("b", "simulate_plan", {"demand_id": str(row.id)})
    assert not plan.get("isError"), plan
    assert use("a", "simulate_plan", {"demand_id": str(row.id)}).get("isError")
    case_id = plan["structuredContent"]["case_id"]
    option_id = plan["structuredContent"]["options"][0]["id"]
    compared = use(
        "b", "compare_plan_options", {"case_id": case_id, "option_ids": [option_id]}
    )
    assert compared["structuredContent"]["options"][0]["id"] == option_id
    denied = use(
        "a", "compare_plan_options", {"case_id": case_id, "option_ids": [option_id]}
    )
    assert denied.get("isError")
    for role in ("worker", "outside", "foreign"):
        assert use(role, "read_coordination_context", {"demand_id": str(row.id)}).get(
            "isError"
        )
    foreign = principal(db, people["foreign"].org_id, people["foreign"].id)
    assert "simulate_plan" not in available_tools(foreign, db)
    worker = principal(db, row.org_id, people["E04"].id)
    assert "simulate_plan" not in available_tools(worker, db)
    after = business_rows(db, row.org_id)
    for snapshot in (before, after):
        snapshot["audit_event"] = json.dumps(
            [
                event
                for event in json.loads(snapshot["audit_event"])
                if event["action"] != "mcp.tool_called"
            ],
            sort_keys=True,
        )
    assert after == before
    assert [
        n.model_dump_json() for n in db.exec(notifications).all()
    ] == before_notifications
    setting = db.exec(
        select(CapacitySetting).where(CapacitySetting.org_id == row.org_id)
    ).one()
    setting.enabled = False
    db.commit()
    listing = client.post(
        "/mcp",
        headers=headers["b"],
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    ).json()
    assert not {"simulate_plan", "compare_plan_options"} & {
        tool["name"] for tool in listing["result"]["tools"]
    }
