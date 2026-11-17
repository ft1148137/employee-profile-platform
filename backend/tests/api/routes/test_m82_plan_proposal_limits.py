import json
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.domains.bot import schemas as s
from app.domains.bot import store, tools
from app.domains.capacity import assistant, demand
from app.domains.capacity.calendar import this_week
from app.domains.capacity.demand_schemas import DemandInput
from app.domains.capacity.models import (
    CapacityDemandEvent,
    CapacityWeek,
    DemandEventKind,
)
from app.domains.planning import service
from app.domains.planning.schemas import AssigneeInput, OptionInput
from tests.api.routes.test_m82_assistant_copy import NOTICE
from tests.api.routes.test_m82_assistant_copy import m82 as m82
from tests.domains.test_im_gateway import principal


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("conditional", [False, True])
@pytest.mark.parametrize("length", ["boundary", "one_over", "full_input"])
def test_plan_proposal_length_budget(
    m82: tuple, db: Session, channel: str, conditional: bool, length: str
) -> None:
    people, args, _ = m82
    a = principal(db, people["E02"].org_id, people["E02"].id)
    b = principal(db, a.org_id, people["E05"].id)
    start = this_week(db, a.org_id)
    for week in (start, start + timedelta(days=7)):
        db.add(
            CapacityWeek(
                org_id=a.org_id,
                employee_id=people["E05"].id,
                week_start=week,
                other_percent=0,
                confirmed_by_user_id=b.user_id,
            )
        )
    db.flush()
    submitted = demand.submit_demand(
        a,
        db,
        **DemandInput.model_validate(
            {
                **args,
                "capability_key": "model_serving",
                "window_start": start,
                "window_end": start + timedelta(days=11),
                "headcount": 1,
            }
        ).model_dump(),
    )
    case = service.create_case(b, db, submitted.id)
    option = service.create_option(
        b,
        db,
        service.authorized_case(b, db, case.id),
        OptionInput(
            assignees=[
                AssigneeInput(
                    employee_id=people["E05"].id,
                    allocation_percent=30 if conditional else 60,
                    start_date=start,
                    end_date=start + timedelta(days=12 if conditional else 11),
                )
            ]
        ),
    )
    conditions = option.summary["conditions"]
    if conditional:
        assert conditions == ["需求方要接受新日期", "需求方要同意缩小投入比例"]
        assert option.verdict == "conditional"
    else:
        assert conditions == [] and option.verdict == "feasible"
    suffix = "\n条件：" + ("；".join(conditions) or "无")
    budget = 2000 - len(suffix)
    size = {"boundary": budget, "one_over": budget + 1, "full_input": 2000}[length]
    text = "合" * size
    session = store.create_session(db, b, channel=channel)
    cards: list[s.Message] = []
    arguments = {"case_id": str(case.id), "option_id": str(option.id), "text": text}
    result = tools.call(db, b, session, cards, "prepare_plan_proposal", arguments)
    if size > budget:
        assert result == {
            "error": (
                f"说明与完整方案条件合计不能超过 2000 字，本次说明最多可写 {budget} 字，"
                f"当前超出 {size - budget} 字。请缩短说明后重新预览，保留全部方案条件。"
            )
        }
        assert not cards
        assert demand.get_demand(a, db, submitted.id).status == "submitted"
        assert not db.exec(
            select(CapacityDemandEvent).where(
                CapacityDemandEvent.org_id == a.org_id,
                CapacityDemandEvent.demand_id == submitted.id,
                CapacityDemandEvent.kind == DemandEventKind.REPLIED,
            )
        ).all()
        text = text[:budget]
        result = tools.call(
            db, b, session, cards, "prepare_plan_proposal", {**arguments, "text": text}
        )
    assert result["status"] == "pending", result
    assert len(cards) == 1
    card = cards[0]
    assert isinstance(card, s.CapacityCard)
    assert card.arguments["text"] == text + suffix
    assert len(card.arguments["text"]) == 2000
    assert card.arguments["outcome"] == ("partial" if conditional else "can_do")
    assert suffix + "\n说明：" in card.content
    assert text in card.content and NOTICE in card.content
    raw = json.dumps(card.model_dump(mode="json"), ensure_ascii=False)
    assert people["E05"].display_name not in raw
    assert str(people["E05"].id) not in raw
    assert demand.get_demand(a, db, submitted.id).status == "submitted"
    assert not db.exec(
        select(CapacityDemandEvent).where(
            CapacityDemandEvent.org_id == a.org_id,
            CapacityDemandEvent.demand_id == submitted.id,
            CapacityDemandEvent.kind == DemandEventKind.REPLIED,
        )
    ).all()
    confirmed = assistant.act(db, b, session, cards, card.actions[0])
    assert confirmed.status == "confirmed", confirmed.result
    reply = db.exec(
        select(CapacityDemandEvent).where(
            CapacityDemandEvent.org_id == a.org_id,
            CapacityDemandEvent.demand_id == submitted.id,
            CapacityDemandEvent.kind == DemandEventKind.REPLIED,
        )
    ).one()
    assert reply.text == text + suffix
    assert demand.get_demand(a, db, submitted.id).status == "replied"
