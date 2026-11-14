from typing import Literal

import pytest
from sqlmodel import Session

from app.domains.bot import core, store, tools
from app.domains.bot import schemas as s
from app.domains.change_request import employee
from app.domains.change_request.models import ProfileChangeRequest, RequestStatus
from app.domains.change_request.schemas import ItemInput
from app.domains.im import actions
from app.domains.im.adapters.base import Event
from app.domains.model.pi_client import TurnResult
from app.domains.review.models import ApprovalPolicy
from tests.api.routes.test_employee_requests import payload, principal_for
from tests.domains.test_bot_core import enable


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("tool", ["get_my_requests", "preview_request"])
@pytest.mark.parametrize("status", [RequestStatus.DRAFT, RequestStatus.PENDING_OWNER])
@pytest.mark.parametrize("overlap", [False, True])
def test_stale_draft_card(
    demo: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu"],
    tool: str,
    status: RequestStatus,
    overlap: bool,
) -> None:
    p = principal_for(db, demo[1]["E02"].org_id)
    enable(db, p.org_id)
    stale = employee.save_draft(
        p,
        db,
        None,
        [ItemInput(**payload()), ItemInput(**payload("frontend_ui"))],
        "web",
    )
    unrelated = employee.save_draft(
        p, db, None, [ItemInput(**payload("incident_support"))], "web"
    )
    row = db.get(ProfileChangeRequest, stale.id)
    assert row
    row.status = status
    db.add(row)
    pending = employee.save_draft(
        p,
        db,
        None,
        [ItemInput(**payload("automated_testing" if overlap else "model_training"))],
        "web",
    )
    employee.submit(p, db, pending.id, pending.revision, "web")
    db.commit()
    before = employee.my_requests(p, db, "all")
    captured: list[dict] = []

    def respond(*_args: object, **kwargs: object) -> TurnResult:
        call = kwargs["on_tool_call"]
        captured.append(
            call(
                tool, {} if tool == "get_my_requests" else {"request_id": str(stale.id)}
            )
        )
        return TurnResult(
            text="请查看请求状态", stop_reason="stop", model_calls=1, tool_calls=1
        )

    monkeypatch.setattr(core.pi_client, "run_turn", respond)
    if channel == "web":
        session = store.create_session(db, p, channel)
        response = core.turn(db, p, session.id, "查看我的请求")
    else:
        reply = actions.text(
            db,
            p,
            Event(
                platform="feishu",
                org_id=p.org_id,
                event_id="stale-draft",
                external_user_id="synthetic-user",
                external_chat_id="synthetic-chat",
                text="查看我的请求",
            ),
            "synthetic-app",
        )
        response = reply.response
    previews = [
        m
        for m in response.messages
        if isinstance(m, s.PreviewCard) and m.request_id == stale.id
    ]
    if tool == "get_my_requests":
        assert any(
            isinstance(m, s.PreviewCard) and m.request_id == unrelated.id
            for m in response.messages
        )
    assert len(previews) == int(not overlap)
    if overlap:
        card = next(
            m
            for m in response.messages
            if isinstance(m, s.StatusCard) and m.id == stale.id
        )
        assert card.status == status and card.actions == []
        notice = captured[0]["blocked_drafts"][0]
        assert notice["request_id"] == str(stale.id)
        assert "自动化测试" in notice["message"]
        assert "已有待审请求" in notice["message"]
        assert "等审完" in notice["message"] and "画像页删除" in notice["message"]
    else:
        assert "blocked_drafts" not in captured[0]
        assert [a.action for a in previews[0].actions] == [
            "confirm_submit",
            "continue_editing",
        ]
    assert employee.my_requests(p, db, "all") == before


@pytest.mark.parametrize("tool", ["get_my_requests", "preview_request"])
def test_decided_item_in_pending_request_does_not_block_draft(
    demo: tuple, db: Session, tool: str
) -> None:
    p = principal_for(db, demo[1]["E02"].org_id)
    db.add(
        ApprovalPolicy(
            org_id=p.org_id,
            version=1,
            created_by_user_id=p.user_id,
            category_overrides={"AI 与数据": False},
        )
    )
    db.commit()
    stale = employee.save_draft(
        p, db, None, [ItemInput(**payload("model_training"))], "web"
    )
    pending = employee.save_draft(
        p,
        db,
        None,
        [ItemInput(**payload()), ItemInput(**payload("model_training"))],
        "web",
    )
    submitted = employee.submit(p, db, pending.id, pending.revision, "web")
    assert (
        submitted.status == "pending_review"
        and submitted.items[1].decision == "approved"
    )
    cards: list[s.Message] = []
    result = tools.call(
        db,
        p,
        store.create_session(db, p),
        cards,
        tool,
        {} if tool == "get_my_requests" else {"request_id": str(stale.id)},
    )
    assert "blocked_drafts" not in result
    assert any(isinstance(c, s.PreviewCard) and c.request_id == stale.id for c in cards)
