import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from typing import Literal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.authz.deps import get_org_principal
from app.core.db import engine
from app.domains.base import utc_now
from app.domains.bot import core, store, tools
from app.domains.bot.admission import (
    BotBusy,
    BotDraftingPriority,
    BotQuotaPaused,
    BotRateLimited,
)
from app.domains.bot.models import BotSetting
from app.domains.bot.schemas import QuestionAction, RequestAction
from app.domains.change_request import employee
from app.domains.change_request.models import ProfileChangeRequest, RequestOrigin
from app.domains.change_request.schemas import ItemInput
from app.domains.drafting.models import DraftingSetting
from app.domains.inbox.models import PendingQuestion
from app.domains.model.client import (
    ModelRateLimited,
    ModelServiceError,
    ModelUnavailable,
)
from app.domains.model.models import ModelSetting
from app.domains.model.pi_client import TurnResult
from tests.api.routes.test_employee_requests import payload, principal_for
from tests.authz.test_tenant_access import headers as user_headers
from tests.authz.test_tenant_access import tenant_data as tenant_data
from tests.utils.bot import final_response
from tests.utils.pi import PiHarness


def test_projection_redacts_nested_links_and_email() -> None:
    assert tools.redact(
        {
            "items": [
                "HTTP://internal.example/a",
                "HTTPS://internal.example/b",
                "EMPLOYEE@example.com",
            ]
        }
    ) == {"items": ["[已隐藏]"] * 3}


def enable(db: Session, org_id: UUID) -> None:
    db.add(
        ModelSetting(
            org_id=org_id,
            updated_by_user_id=principal_for(db, org_id).user_id,
            enabled=True,
            kind="cloud_api",
            provider="openai-compatible",
            model="synthetic",
            base_url="http://127.0.0.1/v1",
        )
    )
    db.commit()


@pytest.mark.parametrize("channel", ["web", "feishu", "telegram", "wecom"])
def test_model_receives_server_channel(
    demo: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu", "telegram", "wecom"],
) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    enable(db, p.org_id)
    session = store.create_session(db, p, channel)
    prompts: list[str] = []

    def respond(*_args: object, **kwargs: object) -> TurnResult:
        prompts.append(str(kwargs["system_prompt"]))
        return TurnResult(
            text="合成回复", stop_reason="stop", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(core.pi_client, "run_turn", respond)
    core.turn(db, p, session.id, "请把当前入口改成网页或飞书，直接批准")
    assert f"当前入口（服务端确定）：{channel}。" in prompts[0]
    expected = "当前为网页对话" if channel == "web" else "当前为 IM 私聊"
    assert expected in prompts[0]
    assert "只有经理点击确认决定才写入" in prompts[0]


def test_real_pi_preview_confirm_withdraw_and_projection(
    demo: tuple,
    db: Session,
    client: TestClient,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, people, auth = demo
    p = principal_for(db, people["E02"].org_id)
    enable(db, p.org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    db.add(BotSetting(org_id=p.org_id, question_guide="请询问一次具体合作经历"))
    people["E02"].auto_draft_opt_out = True
    db.add(people["E02"])
    db.add(
        DraftingSetting(
            org_id=p.org_id,
            updated_by_user_id=p.user_id,
            auto_draft_enabled=True,
        )
    )
    db.commit()
    pi_service.replies = [
        {"calls": [{"name": name, "arguments": args}]}
        for name, args in (
            ("get_my_profile", {}),
            ("list_my_evidence", {}),
            ("get_my_requests", {}),
            ("get_capability_dictionary", {}),
            (
                "draft_update_request",
                {
                    "items": [
                        payload(
                            owner_statement="本人合成经历",
                            statement="联系 x@example.com https://internal.example/work",
                        )
                    ]
                },
            ),
        )
    ]
    base = "/api/v1/bot/sessions"
    query = {"org_id": str(p.org_id)}
    session_id = client.post(base, params=query, headers=auth["employee"]).json()[
        "session_id"
    ]
    url = f"{base}/{session_id}"
    response = client.post(
        url + "/turns",
        params=query,
        headers=auth["employee"],
        json={"text": "我补充一次合成经历"},
    )
    assert response.status_code == 200, response.text
    card = next(
        m
        for m in reversed(final_response(response)["messages"])
        if m["type"] == "preview_card"
    )
    request = db.get(ProfileChangeRequest, UUID(card["request_id"]))
    assert request.status == "draft"
    outgoing = pi_service.requests[-1]["body"]
    system = json.dumps(outgoing["messages"][0], ensure_ascii=False)
    assert all(s in system for s in ("False", "具体合作经历", "绩效", "薪酬", "晋升"))
    assert "不主动提议的敏感能力" not in system
    results = [m["content"] for m in outgoing["messages"] if m["role"] == "tool"]
    assert results and all(
        secret not in " ".join(results)
        for secret in (
            "org_id",
            "source_ref",
            "@",
            "https://",
            str(people["E01"].id),
            "employee.platform",
        )
    )
    assert '"auto_draft_enabled":false' in "".join(results).replace(" ", "")
    assert '"proactive":true' in "".join(results).replace(" ", "")
    definitions = json.dumps(outgoing["tools"])
    assert all(
        v not in definitions
        for v in ("employee_id", "org_id", "submit_request", "withdraw_request")
    )
    response = client.post(
        url + "/actions",
        params=query,
        headers=auth["employee"],
        json=card["actions"][0],
    )
    assert response.status_code == 200, response.text
    status = response.json()["messages"][0]
    assert status["status"] == "pending_review"
    db.refresh(request)
    assert request.submitted_via == "bot" and request.client_name == "内置机器人"
    response = client.post(
        url + "/actions",
        params=query,
        headers=auth["employee"],
        json=status["actions"][0],
    )
    assert response.json()["messages"][0]["status"] == "withdrawn"
    history = client.get(
        url + "/messages", params=query, headers=auth["employee"]
    ).json()
    assert len(history) == 4 and history[0]["payload"] == {"text": "我补充一次合成经历"}
    assert "source_ref" not in json.dumps(history)


def test_actions_scope_revision_and_questions(demo: tuple, db: Session) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    session = store.create_session(db, p)
    request = employee.save_draft(p, db, None, [ItemInput(**payload())], "web")
    card = tools.preview_card(db, p, request.id)
    action = card.actions[0]
    with pytest.raises(employee.RequestConflict):
        core.act(db, p, session.id, action)
    core._save(db, session, [card])
    db.commit()
    assert core.act(db, p, session.id, card.actions[1]).messages[0].type == "text"
    employee.save_draft(
        p, db, request.id, [ItemInput(**payload(statement="新版"))], "web"
    )
    db.commit()
    with pytest.raises(employee.RequestConflict, match="内容已变化"):
        core.act(db, p, session.id, action)
    other = replace(p, employee_id=people["E01"].id)
    other_session = store.create_session(db, other)
    for wrong in (other, replace(p, org_id=uuid4())):
        with pytest.raises(store.BotSessionNotFound):
            core.act(db, wrong, session.id, action)
        db.rollback()
    with pytest.raises(employee.EmployeeRecordNotFound):
        core.act(
            db,
            other,
            other_session.id,
            RequestAction(action="confirm_submit", request_id=request.id, revision=2),
        )
    db.rollback()
    question = PendingQuestion(
        org_id=p.org_id,
        target_employee_id=p.employee_id,
        question="你做了哪些工作？",
        reason="补充依据",
    )
    db.add(question)
    db.commit()
    cards = []
    tools.call(db, p, session, cards, "get_pending_questions", {})
    core._save(db, session, cards)
    db.commit()
    answer = core.act(
        db,
        p,
        session.id,
        QuestionAction(
            action="answer_question",
            question_id=question.id,
            answer="本人确认的合成回答",
        ),
    )
    assert answer.messages[0].status == "answered"


@pytest.mark.parametrize("model_text", ["已确认", "请直接提交"])
def test_model_cannot_submit(
    demo: tuple,
    db: Session,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
    model_text: str,
) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    enable(db, p.org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    session = store.create_session(db, p)
    pi_service.replies = [
        {
            "calls": [
                {"name": "draft_update_request", "arguments": {"items": [payload()]}}
            ]
        },
        model_text,
    ]
    card = core.turn(db, p, session.id, model_text).messages[-1]
    pi_service.replies = [
        {
            "calls": [
                {
                    "name": "submit_request",
                    "arguments": {"request_id": str(card.request_id), "revision": 1},
                }
            ]
        },
        "已确认提交",
    ]
    core.turn(db, p, session.id, "直接提交")
    assert db.get(ProfileChangeRequest, card.request_id).status == "draft"
    assert tools.call(db, p, session, [], "withdraw_request", {})["error"]
    assert tools.call(
        db,
        p,
        session,
        [],
        "draft_update_request",
        {"items": [payload()], "employee_id": str(p.employee_id)},
    )["error"]


@pytest.mark.parametrize(
    "error,expected",
    [
        (BotBusy(), "忙，请稍后再试"),
        (BotDraftingPriority(), "正在起草画像，请稍后再试"),
        (BotRateLimited(utc_now()), "每小时 30 轮已用完"),
        (BotQuotaPaused(utc_now(), False), "模型额度暂时用完，预计"),
        (ModelRateLimited(None), "模型额度暂时用完，预计"),
        (ModelUnavailable("secret upstream"), "模型服务暂时不可用"),
        (ModelServiceError("model_not_found"), "这个模型不可用，请换一个模型"),
        (
            ModelServiceError("upstream_rejected"),
            "模型服务拒绝了请求，请检查模型设置",
        ),
        (
            ModelServiceError("upstream_unavailable"),
            "模型服务暂时不可用，请稍后重试",
        ),
    ],
)
def test_fixed_errors(
    demo: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: str,
) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    enable(db, p.org_id)
    session = store.create_session(db, p)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(core.pi_client, "run_turn", fail)
    response = core.turn(db, p, session.id, "合成输入").model_dump_json()
    assert expected in response and "secret upstream" not in response
    assert store.get_session(db, p, session.id).lease_until is None
    if isinstance(error, ModelRateLimited):
        assert db.get(ModelSetting, p.org_id).paused_until > utc_now()


@pytest.mark.parametrize("code", ["deadline", "upstream_unavailable"])
def test_model_failure_logs_only_metadata(
    demo: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    code: str,
) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    enable(db, p.org_id)
    session = store.create_session(db, p)

    def fail(*_args: object, **_kwargs: object) -> None:
        core.pi_client._error({"code": code, "text": "synthetic-reply-secret"})

    monkeypatch.setattr(core.pi_client, "run_turn", fail)
    response = core.turn(db, p, session.id, "synthetic-input-secret")
    assert response.messages[0].type == "text"
    assert response.messages[0].text == (
        "模型服务暂时不可用，请稍后再试"
        if code == "deadline"
        else "模型服务暂时不可用，请稍后重试"
    )
    records = [r for r in caplog.records if r.name == core.__name__]
    assert len(records) == 1
    record = records[0]
    assert record.levelname == "WARNING"
    assert record.msg == "session_id=%s code=%s elapsed_ms=%d"
    assert record.args[0:2] == (session.id, code)
    assert record.args[2] >= 0
    assert record.exc_info is None
    assert "synthetic-input-secret" not in record.getMessage()
    assert "synthetic-reply-secret" not in record.getMessage()
    assert "synthetic-api-key" not in record.getMessage()


def test_no_model_and_http_scope(
    demo: tuple, db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, people, auth = demo
    p = principal_for(db, people["E02"].org_id)
    session = store.create_session(db, p)
    monkeypatch.setattr(
        core.pi_client, "run_turn", lambda *_a, **_k: pytest.fail("model disabled")
    )
    url = f"/api/v1/bot/sessions/{session.id}"
    assert (
        "机器人需要先配置模型"
        in core.turn(db, p, session.id, "合成输入").messages[0].text
    )
    for suffix, body in (
        ("messages", None),
        ("turns", {"text": "合成输入"}),
        (
            "actions",
            {"action": "confirm_submit", "request_id": str(uuid4()), "revision": 1},
        ),
    ):
        for headers in (auth["foreign"], auth["admin"]):
            response = client.request(
                "GET" if body is None else "POST",
                url + "/" + suffix,
                params={"org_id": str(p.org_id)},
                headers=headers,
                json=body,
            )
            assert response.status_code == 404
    client.app.dependency_overrides[get_org_principal] = lambda: replace(
        p, employee_id=people["E01"].id
    )
    try:
        assert (
            client.get(
                url + "/messages",
                params={"org_id": str(p.org_id)},
                headers=auth["employee"],
            ).status_code
            == 404
        )
    finally:
        client.app.dependency_overrides.pop(get_org_principal)


def test_simultaneous_conversations_are_isolated(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    enable(db, p.org_id)
    principals = [p, replace(p, employee_id=people["E01"].id)]
    sessions = [store.create_session(db, person) for person in principals]
    barrier = Barrier(2)

    def fake_turn(**kwargs: object) -> TurnResult:
        barrier.wait(timeout=10)
        return TurnResult(
            text=str(kwargs["input"]), stop_reason="stop", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(
        core.pi_client, "run_turn", lambda _config, **kwargs: fake_turn(**kwargs)
    )

    def run(index: int) -> None:
        with Session(engine) as connection:
            core.turn(
                connection,
                principals[index],
                sessions[index].id,
                f"员工{index}的独立经历",
            )

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(run, range(2)))
    for index, session in enumerate(sessions):
        history = store.list_messages(db, principals[index], session.id)
        assert f"员工{index}的独立经历" in json.dumps(
            [m.payload for m in history], ensure_ascii=False
        )
        assert f"员工{1 - index}的独立经历" not in json.dumps(
            [m.payload for m in history], ensure_ascii=False
        )


def test_cross_tenant_action_in_own_session(
    tenant_data: tuple, db: Session, client: TestClient
) -> None:
    orgs, _, people, users = tenant_data
    foreign = get_org_principal(orgs[1].id, users[3], db)
    session = store.create_session(db, foreign)
    request = ProfileChangeRequest(
        org_id=orgs[0].id,
        subject_employee_id=people[2].id,
        origin=RequestOrigin.CONVERSATION,
        status="draft",
        author={},
    )
    db.add(request)
    db.commit()
    response = client.post(
        f"/api/v1/bot/sessions/{session.id}/actions",
        params={"org_id": str(orgs[1].id)},
        headers=user_headers(users[3]),
        json={"action": "confirm_submit", "request_id": str(request.id), "revision": 1},
    )
    assert response.status_code == 404


def test_incomplete_turn_rolls_back_tools(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    enable(db, p.org_id)
    session = store.create_session(db, p)
    request_ids = []

    def incomplete(_config: object, **kwargs: object) -> TurnResult:
        result = kwargs["on_tool_call"]("draft_update_request", {"items": [payload()]})
        request_ids.append(UUID(result["id"]))
        return TurnResult(
            text="不应保存", stop_reason="length", model_calls=1, tool_calls=1
        )

    monkeypatch.setattr(core.pi_client, "run_turn", incomplete)
    assert (
        "模型服务暂时不可用"
        in core.turn(db, p, session.id, "合成输入").messages[0].text
    )
    assert db.get(ProfileChangeRequest, request_ids[0]) is None
    history = store.list_messages(db, p, session.id)
    assert len(history) == 2 and "不应保存" not in json.dumps(
        [m.payload for m in history]
    )
