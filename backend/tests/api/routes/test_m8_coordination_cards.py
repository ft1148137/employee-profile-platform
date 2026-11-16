from contextlib import contextmanager
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.security import create_access_token
from app.domains.bot import core, store, tools
from app.domains.bot import schemas as s
from app.domains.bot.admission import BotBusy
from app.domains.capacity.models import CapacityDemand
from app.domains.im.rendering import render
from app.domains.model.client import ModelUnavailable
from tests.api.routes.test_m8_coordination_tools import assert_no_b_people
from tests.domains.test_im_gateway import principal

pytest_plugins = (
    "tests.api.routes.test_m6_capacity_assistant",
    "tests.api.routes.test_m8_coordination_tools",
)


def test_demand_card_marks_unclear_fields_and_keeps_metadata_out_of_submission(
    case: tuple, db: Session
) -> None:
    a, _, args, _ = case
    session = store.create_session(db, a)
    cards: list[s.Message] = []
    result = tools.call(
        db,
        a,
        session,
        cards,
        "prepare_demand",
        {
            **args,
            "unclear_fields": ["headcount", "window_end"],
            "questions": ["需要几个人？", "结束日期是否确定？"],
        },
    )
    assert result["status"] == "pending"
    card = cards[0]
    assert isinstance(card, s.CapacityCard)
    assert card.questions == ["需要几个人？", "结束日期是否确定？"]
    assert {(field.label, field.value) for field in card.fields if field.unclear} == {
        ("人数", "2"),
        ("结束日期", "2026-12-31"),
    }
    assert "unclear_fields" not in card.arguments
    assert "questions" not in card.arguments
    store.append_message(
        db, session, "assistant", s.TurnResponse(messages=cards).model_dump(mode="json")
    )
    db.commit()
    confirmed = core.act(db, a, session.id, card.actions[0])
    assert isinstance(confirmed.messages[0], s.CapacityCard)
    assert confirmed.messages[0].status == "confirmed"
    assert db.exec(
        select(CapacityDemand).where(CapacityDemand.org_id == a.org_id)
    ).first()


def test_only_target_manager_gets_server_plan_card(
    coordination: tuple, db: Session, client: TestClient
) -> None:
    people, _, row = coordination
    a = principal(db, row.org_id, people["E01"].id)
    b = principal(db, row.org_id, people["E05"].id)
    for viewer in (
        a,
        principal(db, row.org_id, people["E04"].id),
        principal(db, row.org_id, people["E10"].id),
    ):
        cards: list[s.Message] = []
        result = tools.call(
            db,
            viewer,
            store.create_session(db, viewer),
            cards,
            "coordination",
            {"function": "simulate_plan", "arguments": {"demand_id": str(row.id)}},
        )
        assert "error" in result
        assert not cards
        assert_no_b_people(result, people)
    cards = []
    result = tools.call(
        db,
        b,
        store.create_session(db, b),
        cards,
        "coordination",
        {"function": "simulate_plan", "arguments": {"demand_id": str(row.id)}},
    )
    assert result["options"]
    assert len(cards) == len(result["options"])
    assert all(isinstance(card, s.PlanCard) for card in cards)
    assert all(
        card.link == f"/capacity?tab=incoming&demand={row.id}&plans=1" for card in cards
    )
    db.commit()
    response = client.get(
        f"/api/v1/orgs/{row.org_id}/planning/cases/{result['case_id']}",
        headers={
            "Authorization": f"Bearer {create_access_token(b.user_id, expires_delta=timedelta(minutes=10))}"
        },
    )
    assert response.status_code == 200, response.text
    options = {option["kind_label"]: option for option in response.json()["options"]}
    for card in cards:
        option = options[card.fields[0].value]
        values = {field.label: field.value for field in card.fields}
        assert values["结论"] == option["verdict_label"]
        assert values["交付日期"] == str(option["summary"]["finish_date"] or "未定")
        assert values["覆盖人数"] == f"{option['summary']['covered_headcount']} 人"
        assert values["缺口"] == f"{option['summary']['gap_person_weeks']} 人周"
        assert (
            values["受影响的工作数"]
            == f"{len(option['summary']['affected_assignment_ids'])} 项"
        )


def test_feishu_renders_card_fields_and_plan_link() -> None:
    demand = s.CapacityCard(
        kind="demand",
        title="提交部门需求",
        content="需求说明",
        arguments={},
        fields=[s.CardField(label="人数", value="2", unclear=True)],
        questions=["人数要确认吗？"],
    )
    demand_text, _ = render(demand)
    assert "人数要确认吗？" in demand_text
    assert "人数：2（请确认）" in demand_text
    plan = s.PlanCard(
        title="直接安排方案",
        fields=[s.CardField(label="缺口", value="1 人周")],
        changes="这次改了什么：交付日期提前",
        link="/capacity?tab=incoming&demand=123&plans=1",
    )
    plan_text, actions = render(plan)
    assert "缺口：1 人周" in plan_text
    assert "这次改了什么：交付日期提前" in plan_text
    assert "/capacity?tab=incoming&demand=123&plans=1" in plan_text
    assert actions == []


def test_compare_revised_option_shows_server_diff(
    coordination: tuple, db: Session, client: TestClient
) -> None:
    people, _, row = coordination
    b = principal(db, row.org_id, people["E05"].id)
    session = store.create_session(db, b)
    cards: list[s.Message] = []
    simulated = tools.call(
        db,
        b,
        session,
        cards,
        "coordination",
        {"function": "simulate_plan", "arguments": {"demand_id": str(row.id)}},
    )
    db.commit()
    headers = {
        "Authorization": f"Bearer {create_access_token(b.user_id, expires_delta=timedelta(minutes=10))}"
    }
    base = client.post(
        f"/api/v1/orgs/{row.org_id}/planning/cases/{simulated['case_id']}/options",
        headers=headers,
        json={
            "assignees": [
                {
                    "employee_id": str(people["E04"].id),
                    "allocation_percent": 50,
                    "start_date": str(row.window_start),
                    "end_date": str(row.window_end),
                }
            ],
        },
    )
    assert base.status_code == 200, base.text
    revised = client.post(
        f"/api/v1/orgs/{row.org_id}/planning/cases/{simulated['case_id']}/options/{base.json()['id']}/revise",
        headers=headers,
        json={"change": "allocation", "allocation_percent": 30},
    )
    assert revised.status_code == 200, revised.text
    cards.clear()
    compared = tools.call(
        db,
        b,
        session,
        cards,
        "coordination",
        {
            "function": "compare_plan_options",
            "arguments": {
                "case_id": simulated["case_id"],
                "option_ids": [revised.json()["id"]],
            },
        },
    )
    assert compared["options"][0]["id"] == revised.json()["id"]
    assert len(cards) == 1 and isinstance(cards[0], s.PlanCard)
    assert "投入" in cards[0].changes
    assert "这次改了什么" in render(cards[0])[0]


def test_model_failure_shows_role_specific_manual_links(
    case: tuple, db: Session, monkeypatch
) -> None:
    a, b, _, _ = case

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise ModelUnavailable()

    monkeypatch.setattr(core.pi_client, "run_turn", unavailable)
    for viewer, expected in ((a, "我提的需求"), (b, "收到的需求")):
        session = store.create_session(db, viewer)
        response = core.turn(db, viewer, session.id, "帮我提需求")
        text = response.messages[0].text
        assert expected in text
        assert "/capacity?tab=" in text


def test_busy_turn_does_not_offer_manual_capacity_fallback(
    case: tuple, db: Session, monkeypatch
) -> None:
    a, _, _, _ = case
    session = store.create_session(db, a)

    @contextmanager
    def busy(*_args: object, **_kwargs: object):
        raise BotBusy()
        yield

    monkeypatch.setattr(core, "begin_turn", busy)
    response = core.turn(db, a, session.id, "帮我提需求")
    assert response.messages[0].text == "忙，请稍后再试"
    assert response.messages[0].capacity_links == []


def test_coordination_tool_failure_discards_partial_plan(
    coordination: tuple, db: Session, monkeypatch
) -> None:
    people, _, row = coordination
    b = principal(db, row.org_id, people["E05"].id)
    from tests.domains.test_bot_core import enable

    enable(db, b.org_id)
    session = store.create_session(db, b)

    def tool_error(*_args: object, **kwargs: object):
        kwargs["on_tool_call"](
            "coordination",
            {"function": "simulate_plan", "arguments": {"demand_id": "invalid"}},
        )
        from app.domains.model.pi_client import TurnResult

        return TurnResult(
            text="半截方案", stop_reason="stop", model_calls=1, tool_calls=1
        )

    monkeypatch.setattr(core.pi_client, "run_turn", tool_error)
    response = core.turn(db, b, session.id, "模拟方案")
    assert "收到的需求" in response.messages[0].text
    assert "半截方案" not in response.messages[0].text
