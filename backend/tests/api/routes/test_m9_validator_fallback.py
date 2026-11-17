from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.domains.bot import schemas as s
from app.domains.bot import store, tools
from app.domains.im.rendering import render
from app.domains.planning import service
from app.domains.planning.schemas import OptionInput, OptionPublic
from app.domains.profile.models import ProfileVersion
from app.domains.review.publish import current_claims
from tests.api.routes.test_m7_planning_readonly import business_rows
from tests.api.routes.test_m8_coordination_tools import assert_no_b_people
from tests.domains.test_im_gateway import principal

pytest_plugins = ("tests.api.routes.test_m8_coordination_tools",)


@pytest.fixture
def feasible_coordination(
    coordination: tuple, db: Session, client: TestClient
) -> tuple:
    people, auth, demand = coordination
    latest = db.exec(
        select(ProfileVersion)
        .where(
            ProfileVersion.org_id == demand.org_id,
            ProfileVersion.subject_employee_id == people["E04"].id,
        )
        .order_by(col(ProfileVersion.revision).desc())
    ).first()
    claims = current_claims(db, demand.org_id, people["E04"].id)
    response = client.post(
        f"/api/v1/orgs/{demand.org_id}/employees/{people['E04'].id}/profile/direct-edits",
        headers=auth["admin"],
        json={
            "base_revision": latest.revision if latest else None,
            "items": [
                {
                    "op": "modify" if demand.capability_key in claims else "add",
                    "capability_key": demand.capability_key,
                    "statement": "合成能力判断",
                    "level": "routine",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    return coordination


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_worse_recommendation_keeps_both_choices(
    feasible_coordination: tuple, db: Session, channel: str
) -> None:
    people, _, demand = feasible_coordination
    manager = principal(db, demand.org_id, people["E05"].id)
    before = business_rows(db, demand.org_id)
    case = service.create_case(manager, db, demand.id)
    option = service.create_option(
        manager,
        db,
        service.authorized_case(manager, db, case.id),
        OptionInput.model_validate(
            {
                "assignees": [
                    {
                        "employee_id": str(people["E04"].id),
                        "allocation_percent": 100,
                        "start_date": demand.window_start.isoformat(),
                        "end_date": demand.window_end.isoformat(),
                    }
                ]
            }
        ),
    )
    assert option.verdict == "infeasible"
    session = store.create_session(db, manager, channel=channel)
    cards: list[s.Message] = []
    result = tools.call(
        db,
        manager,
        session,
        cards,
        "prepare_plan_proposal",
        {
            "case_id": str(case.id),
            "option_id": str(option.id),
            "text": "请核对部门方案",
        },
    )
    assert result["status"] == "pending"
    reply = cards[0]
    assert isinstance(reply, s.CapacityCard)
    assert reply.arguments["outcome"] == "not_now"
    assert reply.plan is not None
    assert reply.plan.fields[1].value == "不可行"
    fallback = reply.plan.rule_fallback
    assert fallback is not None
    assert {f.label for f in fallback.fields} == {
        "类型",
        "结论",
        "交付日期",
        "覆盖人数",
        "缺口",
        "新人数",
    }
    assert fallback.fields[1].value == "可行"
    assert (
        next(f.value for f in fallback.differences if f.label == "结论")
        == "不可行 → 可行"
    )
    link = f"/capacity?tab=incoming&demand={demand.id}&plans=1"
    assert reply.plan.link == fallback.link == link
    text, actions = render(reply)
    assert "规则引擎方案" in text and "不可行 → 可行" in text
    assert text.count(link) == 2
    assert len(text) < 12000 and len(actions) == 2
    cards.clear()
    tools.call(
        db,
        manager,
        session,
        cards,
        "coordination",
        {
            "function": "compare_plan_options",
            "arguments": {"case_id": str(case.id), "option_ids": [str(option.id)]},
        },
    )
    assert isinstance(cards[0], s.PlanCard)
    assert cards[0].rule_fallback == fallback
    assert render(cards[0])[0].count(link) == 2
    assert business_rows(db, demand.org_id) == before
    for code in ("E01", "E04", "E10", "foreign"):
        viewer = principal(db, people[code].org_id, people[code].id)
        denied: list[s.Message] = []
        result = tools.call(
            db,
            viewer,
            store.create_session(db, viewer, channel=channel),
            denied,
            "prepare_plan_proposal",
            {"case_id": str(case.id), "option_id": str(option.id), "text": "合成答复"},
        )
        assert "error" in result and not denied
        assert_no_b_people(result, people)
    assert business_rows(db, demand.org_id) == before


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_equal_or_better_conclusion_has_no_fallback(
    feasible_coordination: tuple, db: Session, channel: str
) -> None:
    people, _, demand = feasible_coordination
    manager = principal(db, demand.org_id, people["E05"].id)
    case = service.create_case(manager, db, demand.id)
    option = next(o for o in case.options if o.verdict == "feasible")
    cards: list[s.Message] = []
    tools.call(
        db,
        manager,
        store.create_session(db, manager, channel=channel),
        cards,
        "prepare_plan_proposal",
        {"case_id": str(case.id), "option_id": str(option.id), "text": "合成答复"},
    )
    assert isinstance(cards[0], s.CapacityCard)
    assert cards[0].plan is None
    assert "规则引擎方案" not in render(cards[0])[0]
    assert tools.plan_card(option, case).rule_fallback is None


def test_fallback_uses_snapshot_rules_in_original_order(
    coordination: tuple, db: Session
) -> None:
    people, _, demand = coordination
    manager = principal(db, demand.org_id, people["E05"].id)
    case = service.create_case(manager, db, demand.id)
    base = case.options[0]

    def option(kind: str, verdict: str, **updates: object) -> OptionPublic:
        return base.model_copy(
            update={"id": uuid4(), "kind": kind, "verdict": verdict, **updates}
        )

    custom = option("custom", "infeasible")
    other_snapshot = option("direct", "feasible", snapshot_id=uuid4())
    ignored_custom = option("custom", "feasible")
    borrow = option("borrow", "conditional")
    equal = option("direct", "conditional")
    case = case.model_copy(
        update={"options": [other_snapshot, ignored_custom, borrow, equal, custom]}
    )
    card = tools.plan_card(custom, case)
    assert card.rule_fallback.fields[0].value == "向其他部门借调"
    for verdict in ("feasible", "conditional", "insufficient_data", "infeasible"):
        selected = custom.model_copy(update={"verdict": verdict})
        assert (tools.plan_card(selected, case).rule_fallback is not None) == (
            verdict in ("insufficient_data", "infeasible")
        )
