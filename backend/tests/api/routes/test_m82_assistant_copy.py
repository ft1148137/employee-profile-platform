import json
from collections.abc import Generator
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app.core.security import create_access_token
from app.domains.bot import core, prompts, store, tools
from app.domains.bot import schemas as s
from app.domains.capacity import assistant, demand
from app.domains.capacity.calendar import this_week
from app.domains.capacity.demand_schemas import DemandInput
from app.domains.capacity.models import (
    CapacityDemand,
    CapacityDemandEvent,
    CapacitySetting,
    CapacityWeek,
)
from app.domains.im.rendering import render
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import Department, OrgMembership, RoleGrant
from app.domains.planning import service
from app.domains.planning.models import PlanningCase, PlanningOption, PlanningSnapshot
from app.domains.planning.schemas import AssigneeInput, OptionInput
from app.models import User
from tests.domains.test_bot_core import enable
from tests.domains.test_im_gateway import principal

NOTICE = "确认后把答复发给需求方，不分配人员，不改其他承诺。"
NOT_FOUND = "未找到该需求，或你无权查看（仅发起人及双方部门经理可见）。"


@pytest.fixture
def m82(demo: tuple, db: Session, client: TestClient) -> Generator[tuple]:
    url, people, auth = demo
    org_id = people["E02"].org_id
    source = db.get(Department, people["E02"].department_id)
    assert source
    source.name = "合成企划部门"
    db.add(source)
    target = Department(org_id=org_id, name="合成承接部门")
    db.add(target)
    db.flush()
    child = Department(
        org_id=org_id, name="合成承接下级", parent_department_id=target.id
    )
    db.add(child)
    db.flush()
    for code, dept in (("E04", target.id), ("E05", target.id), ("E10", child.id)):
        people[code].department_id = dept
        db.add(people[code])
    for code, dept in (
        ("E02", source.id),
        ("E03", None),
        ("E05", target.id),
        ("E10", child.id),
    ):
        member = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == org_id,
                OrgMembership.employee_id == people[code].id,
            )
        ).one_or_none()
        if member is None:
            user = User(
                username=str(uuid4()),
                email=f"{uuid4()}@example.com",
                hashed_password="unused",
            )
            db.add(user)
            db.flush()
            member = OrgMembership(
                org_id=org_id, user_id=user.id, employee_id=people[code].id
            )
            db.add(member)
            db.flush()
        if dept is not None:
            db.add(
                RoleGrant(
                    org_id=org_id,
                    membership_id=member.id,
                    role="dept_manager",
                    department_id=dept,
                )
            )
    a = principal(db, org_id, people["E02"].id)
    setting = CapacitySetting(
        org_id=org_id,
        enabled=True,
        requester_policy="managers",
        updated_by_user_id=a.user_id,
    )
    db.add(setting)
    capability = client.get(f"{url}/capabilities", headers=auth["admin"]).json()[
        "data"
    ][0]
    start = this_week(db, org_id)
    for week in (start, start + timedelta(days=7)):
        db.add(
            CapacityWeek(
                org_id=org_id,
                employee_id=people["E04"].id,
                week_start=week,
                other_percent=0,
                confirmed_by_user_id=a.user_id,
            )
        )
    db.commit()
    args = {
        "requesting_department_id": str(source.id),
        "target_department_id": str(target.id),
        "capability_key": capability["key"],
        "headcount": 2,
        "allocation_percent": 60,
        "window_start": "2026-12-01",
        "window_end": "2026-12-11",
        "description": "合成促销加工需求",
    }
    yield people, args, setting
    db.rollback()
    for model in (
        PlanningOption,
        PlanningSnapshot,
        PlanningCase,
        CapacityWeek,
        CapacityDemandEvent,
        CapacityDemand,
        CapacitySetting,
    ):
        db.exec(delete(model).where(model.org_id == org_id))
    db.commit()


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_manager_gets_one_demand_card(
    m82: tuple, db: Session, monkeypatch: pytest.MonkeyPatch, channel: str
) -> None:
    people, args, _ = m82
    a = principal(db, people["E02"].org_id, people["E02"].id)
    enable(db, a.org_id)
    session = store.create_session(db, a, channel=channel)

    def model(*_args: Any, **kwargs: Any) -> TurnResult:
        prompt = kwargs["system_prompt"]
        assert "合成企划部门" in prompt and args["requesting_department_id"] in prompt
        assert "只有一个时默认使用" in prompt
        assert "没写年份" in prompt and "unclear_fields" in prompt
        assert "只有工具明确返回权限错误" in prompt
        result = kwargs["on_tool_call"](
            "prepare_demand",
            {
                **args,
                "unclear_fields": ["window_start", "window_end"],
            },
        )
        assert result["status"] == "pending", result
        assert "没有权限" not in result["preview"]
        return TurnResult(
            text="请核对确认卡", stop_reason="stop", model_calls=1, tool_calls=1
        )

    monkeypatch.setattr(core.pi_client, "run_turn", model)
    response = core.turn(db, a, session.id, "12 月 1 日到 11 日，要 2 人，每人六成时间")
    cards = [m for m in response.messages if isinstance(m, s.CapacityCard)]
    assert len(cards) == 1 and len(response.steps) == 1 and response.steps[0].ok
    assert {f.label for f in cards[0].fields if f.unclear} == {"开始日期", "结束日期"}
    assert not db.exec(
        select(CapacityDemand).where(CapacityDemand.org_id == a.org_id)
    ).all()


def test_wrong_requesting_department_is_still_rejected(m82: tuple, db: Session) -> None:
    people, args, _ = m82
    a = principal(db, people["E02"].org_id, people["E02"].id)
    cards: list[s.Message] = []
    result = tools.call(
        db,
        a,
        store.create_session(db, a),
        cards,
        "prepare_demand",
        {
            **args,
            "requesting_department_id": str(people["E10"].department_id),
        },
    )
    assert result == {"error": "需求方部门只能选择：合成企划部门"}
    assert not cards
    assert not db.exec(
        select(CapacityDemand).where(CapacityDemand.org_id == a.org_id)
    ).all()


def test_employee_cannot_prepare_and_non_permission_errors_stay_specific(
    m82: tuple, db: Session
) -> None:
    people, args, _ = m82
    p = principal(db, people["E03"].org_id, people["E03"].id)
    assert "prepare_demand" not in {t.name for t in assistant.definitions(db, p)}
    session = store.create_session(db, p)
    cards: list[s.Message] = []
    assert tools.call(db, p, session, cards, "prepare_demand", args) == {
        "error": "没有权限执行此操作"
    }
    assert not cards
    manager = principal(db, p.org_id, people["E02"].id)
    assert tools.call(db, manager, session, cards, "submit_demand", args) == {
        "error": "请使用预览工具，并点击卡片确认"
    }
    error = tools.call(
        db,
        manager,
        session,
        cards,
        "prepare_demand",
        {**args, "window_end": "2026-11-01"},
    )
    assert error == {"error": "工具参数不正确"}
    assert "权限" not in error["error"] and not cards


def test_conditional_proposal_separates_conditions_and_confirmation(
    m82: tuple, db: Session
) -> None:
    people, args, _ = m82
    org_id = people["E02"].org_id
    a = principal(db, org_id, people["E02"].id)
    b = principal(db, org_id, people["E05"].id)
    start = this_week(db, org_id)
    submitted = demand.submit_demand(
        a,
        db,
        **DemandInput.model_validate(
            {
                **args,
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
                    employee_id=people["E04"].id,
                    allocation_percent=30,
                    start_date=start,
                    end_date=start + timedelta(days=11),
                ),
            ]
        ),
    )
    assert option.verdict == "conditional"
    conditions = option.summary["conditions"]
    assert "需求方要同意缩小投入比例" in conditions
    db.commit()
    for channel in ("web", "feishu"):
        cards: list[s.Message] = []
        result = tools.call(
            db,
            b,
            store.create_session(db, b, channel=channel),
            cards,
            "prepare_plan_proposal",
            {
                "case_id": str(case.id),
                "option_id": str(option.id),
                "text": "部门可按期承接，请确认投入调整",
            },
        )
        assert result["status"] == "pending", result
        card = cards[0]
        assert isinstance(card, s.CapacityCard)
        assert "结论：能做一部分：" in card.content
        assert f"承诺日期：{start} 至 {start + timedelta(days=11)}" in card.content
        assert "\n条件：" in card.content
        for condition in conditions:
            assert condition in card.content.split("\n条件：")[1].split("\n说明：")[0]
        assert NOTICE in card.content and NOTICE in render(card)[0]
        assert card.arguments["outcome"] == "partial"
        assert "需求方要同意缩小投入比例" in card.arguments["text"]
        raw = json.dumps(card.model_dump(mode="json"), ensure_ascii=False)
        assert (
            people["E04"].display_name not in raw and str(people["E04"].id) not in raw
        )
    assert demand.get_demand(a, db, submitted.id).status == "submitted"


def test_reply_without_conditions_says_none(m82: tuple, db: Session) -> None:
    people, args, _ = m82
    a = principal(db, people["E02"].org_id, people["E02"].id)
    b = principal(db, a.org_id, people["E05"].id)
    row = demand.submit_demand(a, db, **DemandInput.model_validate(args).model_dump())
    result = tools.call(
        db,
        b,
        store.create_session(db, b),
        cards := [],
        "prepare_demand_reply",
        {
            "demand_id": str(row.id),
            "revision": row.revision,
            "outcome": "can_do",
            "text": "部门可承接",
            "start_date": args["window_start"],
            "end_date": args["window_end"],
        },
    )
    assert result["status"] == "pending"
    assert "\n条件：无\n" in cards[0].content and NOTICE in cards[0].content


@pytest.mark.parametrize("viewer", ["E03", "E10"])
def test_hidden_and_missing_demands_have_identical_explanation(
    m82: tuple, db: Session, client: TestClient, viewer: str
) -> None:
    people, args, _ = m82
    a = principal(db, people["E02"].org_id, people["E02"].id)
    row = demand.submit_demand(a, db, **DemandInput.model_validate(args).model_dump())
    p = principal(db, a.org_id, people[viewer].id)
    db.commit()
    for demand_id in (row.id, uuid4()):
        with pytest.raises(demand.CapacityNotFound) as error:
            demand.demand_row(p, db, demand_id)
        assert error.value.status_code == 404 and error.value.detail == NOT_FOUND
        result = tools.call(
            db,
            p,
            store.create_session(db, p),
            [],
            "get_demand",
            {"demand_id": str(demand_id)},
        )
        assert result == {"error": NOT_FOUND}
        headers = {
            "Authorization": f"Bearer {create_access_token(p.user_id, expires_delta=timedelta(minutes=5))}"
        }
        response = client.get(
            f"/api/v1/orgs/{p.org_id}/capacity/demands/{demand_id}", headers=headers
        )
        assert response.status_code == 404 and response.json()["detail"] == NOT_FOUND


def test_assignment_status_requires_current_formal_records(
    m82: tuple, db: Session
) -> None:
    people, _, _ = m82
    p = principal(db, people["E05"].org_id, people["E05"].id)
    for channel in ("web", "feishu"):
        prompt = prompts.system_prompt(db, p, channel)
        assert "说分配状态前先读正式记录" in prompt
        assert "list_my_assignments" in prompt and "只含本人" in prompt
        assert (
            "read_coordination_context、get_approval_status、get_demand 不含正式分配名单"
            in prompt
        )
        assert "未核实，请在网页的需求详情页查看「部门内分配」" in prompt
        assert "不要要求用户反复提供编号或链接" in prompt
