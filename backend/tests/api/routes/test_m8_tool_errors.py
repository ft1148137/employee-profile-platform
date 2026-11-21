from typing import Any

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app.domains.bot import core, store, tools
from app.domains.bot import schemas as s
from app.domains.model.pi_client import TurnResult
from app.domains.planning import service
from app.domains.planning.models import PlanningCase
from tests.api.routes.test_m7_planning_readonly import business_rows
from tests.domains.test_bot_core import enable
from tests.domains.test_im_gateway import principal

pytest_plugins = ("tests.api.routes.test_m8_coordination_tools",)


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("mistake", ["arguments", "stale", "name"])
def test_model_corrects_business_error_in_same_turn(
    coordination: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    channel: str,
    mistake: str,
) -> None:
    people, _, row = coordination
    b = principal(db, row.org_id, people["E05"].id)
    enable(db, row.org_id)
    session = store.create_session(db, b, channel=channel)

    def fake_turn(*_args: Any, **kwargs: Any) -> TurnResult:
        use = kwargs["on_tool_call"]

        def coordinate(function: str, arguments: dict) -> dict:
            return use("coordination", {"function": function, "arguments": arguments})

        if mistake == "arguments":
            error = coordinate("simulate_plan", {"demand_id": "invalid"})
            assert error == {"error": "工具参数不正确"}
        plan = coordinate("simulate_plan", {"demand_id": str(row.id)})
        assert "case_id" in plan
        if mistake == "stale":
            row.revision += 1
            db.add(row)
            db.flush()
            assignees = [
                {
                    "employee_id": str(people["E04"].id),
                    "allocation_percent": 50,
                    "start_date": str(row.window_start),
                    "end_date": str(row.window_end),
                }
            ]
            error = coordinate(
                "simulate_plan",
                {
                    "demand_id": str(row.id),
                    "assignees": assignees,
                },
            )
            assert error == {"error": service.PlanningStale().detail}
            plan = coordinate("simulate_plan", {"demand_id": str(row.id)})
            assert plan["stale"] is False
        option_id = plan["options"][0]["id"]
        compared = coordinate(
            "compare_plan_options",
            {
                "case_id": plan["case_id"],
                "option_ids": [option_id],
            },
        )
        assert compared["options"][0]["id"] == option_id
        if mistake == "name":
            proposal = {"case_id": plan["case_id"], "option_id": option_id}
            before = business_rows(db, row.org_id)
            error = use(
                "prepare_plan_proposal",
                {
                    **proposal,
                    "text": f"安排{people['E04'].display_name}",
                },
            )
            assert error == {"error": "答复只写部门层面的结论，不写人名"}
            missing_reason = use(
                "prepare_plan_proposal",
                {
                    **proposal,
                    "text": "部门可在约定时间交付",
                },
            )
            assert (
                "原因类型" in missing_reason["error"]
                and "card_id" not in missing_reason
            )
            assert business_rows(db, row.org_id) == before
            reply = {
                "demand_id": str(row.id),
                "revision": row.revision,
                "outcome": "not_now",
                "text": "需要核实是否达到要求的能力等级",
            }
            unverified = use("prepare_demand_reply", {**reply, "reason_code": "skill"})
            assert "其他" in unverified["error"] and "card_id" not in unverified
            result = use("prepare_demand_reply", {**reply, "reason_code": "other"})
            assert result["status"] == "pending"
            assert business_rows(db, row.org_id) == before
        return TurnResult(
            text="已修正方案",
            stop_reason="stop",
            model_calls=2,
            tool_calls=7 if mistake == "name" else 5,
        )

    monkeypatch.setattr(core.pi_client, "run_turn", fake_turn)
    response = core.turn(db, b, session.id, "请协调")
    assert response.messages[0].text == "已修正方案"
    assert any(not step.ok for step in response.steps)
    assert response.steps[-1].ok
    assert all(
        not m.capacity_links for m in response.messages if isinstance(m, s.TextMessage)
    )
    assert any(isinstance(m, s.PlanCard) for m in response.messages)
    if mistake == "name":
        assert sum(isinstance(m, s.CapacityCard) for m in response.messages) == 1
        assert any(
            isinstance(m, s.CapacityCard) and m.status == "pending"
            for m in response.messages
        )
    db.refresh(row)
    assert row.status == "submitted"


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("private database detail"),
        HTTPException(503, "private upstream detail"),
    ],
)
def test_system_error_ends_turn_and_rolls_back(
    coordination: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    people, _, row = coordination
    b = principal(db, row.org_id, people["E05"].id)
    enable(db, row.org_id)
    session = store.create_session(db, b)
    create_case = service.create_case

    def fail(*args: Any, **kwargs: Any) -> None:
        create_case(*args, **kwargs)
        raise failure

    def fake_turn(*_args: Any, **kwargs: Any) -> TurnResult:
        kwargs["on_tool_call"](
            "coordination",
            {
                "function": "simulate_plan",
                "arguments": {"demand_id": str(row.id)},
            },
        )
        pytest.fail("System failures must stop the model turn")

    monkeypatch.setattr(service, "create_case", fail)
    monkeypatch.setattr(core.pi_client, "run_turn", fake_turn)
    response = core.turn(db, b, session.id, "请协调")
    assert "模型服务暂时不可用" in response.messages[0].text
    assert "「协同 → 待审批」" in response.messages[0].text
    assert "/reviews?type=demand" in response.messages[0].text
    assert "private" not in response.model_dump_json()
    assert len(response.messages) == 1
    assert (
        db.exec(select(PlanningCase).where(PlanningCase.org_id == row.org_id)).all()
        == []
    )


def test_plan_card_diff_keeps_zero_and_none_distinct(
    coordination: tuple, db: Session
) -> None:
    people, _, row = coordination
    b = principal(db, row.org_id, people["E05"].id)
    case = service.create_case(b, db, row.id)
    option = case.options[0]
    option.summary["diff"] = {
        "gap_person_weeks": {"before": 1, "after": 0},
        "covered_headcount": {"before": 0, "after": 1},
        "finish_date": {"before": None, "after": str(row.window_end)},
    }
    card = tools.plan_card(option, case)
    assert "缺口：1 人周 → 0 人周" in card.changes
    assert "覆盖人数：0 人 → 1 人" in card.changes
    assert f"交付日期：未定 → {row.window_end}" in card.changes
