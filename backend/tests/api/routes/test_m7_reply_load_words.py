import json
from collections.abc import Generator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app.authz.deps import get_org_principal
from app.core.security import create_access_token
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.bot import schemas as bot_schemas
from app.domains.bot.prompts import system_prompt
from app.domains.capacity import assistant
from app.domains.capacity.models import (
    CapacityDemand,
    CapacityDemandEvent,
    CapacitySetting,
)
from app.domains.org.models import Department, OrgMembership, RoleGrant
from app.domains.profile.models import ProfileVersion
from app.models import User
from tests.api.routes.test_mcp import call, initialize, token_for

MESSAGE = "回复将发送给对方部门，请勿填写负荷描述，仅说明能否承接、时间及条件"
LOAD_TEXTS = (
    "目前超负荷",
    "目前基本满",
    "目前有余量",
    "负荷未确认",
    "负荷尚未确认",
    "负荷都未确认",
    "负荷情况目前尚未确认",
)
NEW_LOAD_TEXTS = (
    "目前工作安排基本饱和",
    "人手已满",
    "已经满负荷",
    "团队满员",
    "日程排满",
    "人手紧",
    "人手不足",
    "忙不过来",
    "近期空闲",
    "有空",
    "资源闲置",
    "仍有余量",
)
ASSISTANT_LOAD_TEXTS = NEW_LOAD_TEXTS[:5] + ("忙不过来",)
REPLY = {
    "outcome": "can_do",
    "start_date": "2026-12-01",
    "end_date": "2026-12-31",
    "text": "可以承接",
    "revision": 1,
}


@pytest.fixture
def case(
    minimal_demo: tuple, db: Session, client: TestClient, match_count: int
) -> Generator[tuple]:
    url, people, auth = minimal_demo
    org_id = people["E01"].org_id
    target = Department(org_id=org_id, name="合成承接部门")
    db.add(target)
    db.flush()
    child = Department(
        org_id=org_id, name="合成下级部门", parent_department_id=target.id
    )
    db.add(child)
    db.flush()
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.employee_id == people["E05"].id,
        )
    ).one()
    people["E05"].department_id = target.id
    db.add(people["E05"])
    db.add(
        RoleGrant(
            org_id=org_id,
            membership_id=member.id,
            role="dept_manager",
            department_id=target.id,
        )
    )
    db.add(
        CapacitySetting(org_id=org_id, enabled=True, updated_by_user_id=member.user_id)
    )
    dictionary = client.get(f"{url}/capabilities", headers=auth["admin"]).json()
    capability = dictionary["data"][0]
    for index, code in enumerate(("E04", "E06", "E07")[:match_count]):
        person = people[code]
        person.department_id = child.id if index % 2 else target.id
        db.add(person)
        db.add(
            ProfileVersion(
                org_id=org_id,
                subject_employee_id=person.id,
                revision=99,
                published_at=utc_now(),
                schema_version_id=dictionary["schema_version"]["id"],
                published_by={"actor_type": "system_worker"},
                snapshot={
                    "claims": [
                        {
                            "id": str(uuid4()),
                            "capability_definition_id": capability["id"],
                            "statement": "合成能力依据",
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
    b = get_org_principal(org_id, db.get_one(User, member.user_id), db)
    auth["b"] = {
        "Authorization": f"Bearer {create_access_token(member.user_id, expires_delta=timedelta(minutes=10))}"
    }
    body = {
        "requesting_department_id": str(people["E02"].department_id),
        "target_department_id": str(target.id),
        "capability_key": capability["key"],
        "minimum_level": "routine",
        "headcount": 1,
        "allocation_percent": 50,
        "window_start": "2026-12-01",
        "window_end": "2026-12-31",
        "description": "合成需求",
    }
    headers = initialize(client, token_for(db, people["E05"])[0])
    yield url, auth, b, body, headers
    db.rollback()
    for model in (CapacityDemandEvent, CapacityDemand, CapacitySetting):
        db.exec(delete(model).where(model.org_id == org_id))
    db.commit()


def snapshot(db: Session, org_id: UUID) -> list:
    db.expire_all()
    return [
        [
            row.model_dump(mode="json")
            for row in db.exec(
                select(model).where(model.org_id == org_id).order_by(model.id)
            ).all()
            if not isinstance(row, AuditEvent) or row.action.startswith("capacity.")
        ]
        for model in (CapacityDemand, CapacityDemandEvent, AuditEvent)
    ]


def create_demand(case: tuple, client: TestClient, match_count: int) -> str:
    url, auth, _, body, _ = case
    created = client.post(
        f"{url}/capacity/demands", headers=auth["employee"], json=body
    )
    assert created.status_code == 200, created.text
    demand_id = created.json()["id"]
    basis = client.get(f"{url}/capacity/demands/{demand_id}/basis", headers=auth["b"])
    assert basis.status_code == 200, basis.text
    assert basis.json()["reference"]["count"] == match_count
    return demand_id


def send_reply(
    case: tuple,
    db: Session,
    client: TestClient,
    channel: str,
    demand_id: str,
    text: str,
    *,
    rejected: bool,
) -> None:
    url, auth, b, _, headers = case
    args = {**REPLY, "demand_id": demand_id, "text": text}
    if channel == "assistant":
        cards: list[bot_schemas.Message] = []
        result = assistant.call(db, b, cards, "prepare_demand_reply", args)
        if rejected:
            assert result == {"error": MESSAGE}
            assert not cards
        else:
            assert isinstance(result, dict) and "error" not in result, result
            assert len(cards) == 1
    elif channel == "mcp":
        result = call(client, headers, "reply_demand", args)
        if rejected:
            assert result["isError"], result
            assert json.loads(result["content"][0]["text"]) == {
                "status": 422,
                "detail": MESSAGE,
            }
        else:
            assert not result.get("isError"), result
    else:
        response = client.post(
            f"{url}/capacity/demands/{demand_id}/reply",
            headers=auth["b"],
            json={**REPLY, "text": text},
        )
        assert response.status_code == (422 if rejected else 200), response.text
        if rejected:
            assert response.json()["detail"] == MESSAGE


@pytest.mark.parametrize("match_count", [0, 1, 2])
@pytest.mark.parametrize("channel", ["rest", "assistant", "mcp"])
def test_small_department_rejects_load_words_without_writes(
    case: tuple,
    db: Session,
    client: TestClient,
    match_count: int,
    channel: str,
) -> None:
    demand_id = create_demand(case, client, match_count)
    before = snapshot(db, case[2].org_id)
    for attempt, text in enumerate(LOAD_TEXTS, 1):
        send_reply(case, db, client, channel, demand_id, text, rejected=True)
        assert snapshot(db, case[2].org_id) == before
        if channel == "mcp":
            calls = db.exec(
                select(AuditEvent).where(
                    AuditEvent.org_id == case[2].org_id,
                    AuditEvent.action == "mcp.tool_called",
                )
            ).all()
            assert len(calls) == attempt
            assert all(
                event.target_id == "reply_demand"
                and event.details == {"request_id": "2", "client_name": "Cursor"}
                for event in calls
            )


@pytest.mark.parametrize("match_count", [3])
@pytest.mark.parametrize("channel", ["rest", "assistant", "mcp"])
def test_three_matches_allow_load_words(
    case: tuple,
    db: Session,
    client: TestClient,
    match_count: int,
    channel: str,
) -> None:
    for text in LOAD_TEXTS:
        demand_id = create_demand(case, client, match_count)
        send_reply(case, db, client, channel, demand_id, text, rejected=False)


@pytest.mark.parametrize("match_count", [0])
@pytest.mark.parametrize("channel", ["rest", "assistant"])
def test_new_load_words_are_rejected(
    case: tuple,
    db: Session,
    client: TestClient,
    match_count: int,
    channel: str,
) -> None:
    demand_id = create_demand(case, client, match_count)
    before = snapshot(db, case[2].org_id)
    texts = NEW_LOAD_TEXTS if channel == "rest" else ASSISTANT_LOAD_TEXTS
    for text in texts:
        send_reply(case, db, client, channel, demand_id, text, rejected=True)
        assert snapshot(db, case[2].org_id) == before


@pytest.mark.parametrize("match_count", [3])
def test_three_matches_allow_new_load_words(
    case: tuple, db: Session, client: TestClient, match_count: int
) -> None:
    for text in ASSISTANT_LOAD_TEXTS:
        demand_id = create_demand(case, client, match_count)
        send_reply(case, db, client, "rest", demand_id, text, rejected=False)


@pytest.mark.parametrize("match_count", [0])
def test_load_word_false_positives_are_allowed(
    case: tuple, db: Session, client: TestClient, match_count: int
) -> None:
    for text in (
        "我们已满足冷链资质要求，可在 12 月承接",
        "我们有空调维修人员，可以承接",
    ):
        demand_id = create_demand(case, client, match_count)
        send_reply(case, db, client, "rest", demand_id, text, rejected=False)


@pytest.mark.parametrize("match_count", [0])
def test_bot_and_mcp_reply_guidance(
    case: tuple, db: Session, client: TestClient
) -> None:
    guidance = "不要写个人负荷，只写能否承接、时间和条件"
    assert guidance in system_prompt(db, case[2], "web")
    assert "不写任何负荷说法" in system_prompt(db, case[2], "feishu")
    response = client.post(
        "/mcp",
        headers=case[4],
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    )
    assert response.status_code == 200
    reply = next(
        t for t in response.json()["result"]["tools"] if t["name"] == "reply_demand"
    )
    assert guidance in reply["description"]
    assert "不写任何负荷说法" in reply["description"]


@pytest.mark.parametrize("match_count", [0, 1, 2])
@pytest.mark.parametrize("channel", ["rest", "assistant", "mcp"])
def test_unrelated_confirmation_and_sentence_boundaries_are_allowed(
    case: tuple,
    db: Session,
    client: TestClient,
    match_count: int,
    channel: str,
) -> None:
    for text in (
        "日期未确认",
        "开始时间尚未确认",
        "负荷情况目前都尚未确认",
        *(
            f"负荷{separator}日期未确认"
            for separator in ("。", "；", "，", ",", ";", "\n")
        ),
    ):
        demand_id = create_demand(case, client, match_count)
        send_reply(case, db, client, channel, demand_id, text, rejected=False)
