from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
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
@pytest.mark.parametrize("partial", [False, True])
def test_pending_overlap(
    demo: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu"],
    partial: bool,
) -> None:
    p = principal_for(db, demo[1]["E02"].org_id)
    enable(db, p.org_id)
    evidence_id = employee.my_evidence(p, db).data[0].id
    original = payload(
        level="routine", owner_statement="原请求的依据说明", evidence_ids=[evidence_id]
    )
    draft = employee.save_draft(
        p, db, None, [ItemInput(**original), ItemInput(**payload("frontend_ui"))], "web"
    )
    submitted = employee.submit(p, db, draft.id, draft.revision, "web")
    row = db.get(ProfileChangeRequest, submitted.id)
    assert row
    row.submitted_at = datetime(2026, 11, 14, 1, 2, 3, tzinfo=UTC)
    db.add(row)
    db.commit()
    before = employee.my_requests(p, db, "all")
    captured: list[dict] = []

    def respond(*_args: object, **kwargs: object) -> TurnResult:
        items = [payload(statement="新的经历")]
        if partial:
            items.append(payload("incident_support", statement="另一项新经历"))
        captured.append(
            kwargs["on_tool_call"]("draft_update_request", {"items": items})
        )
        return TurnResult(
            text="请查看说明", stop_reason="stop", model_calls=1, tool_calls=1
        )

    monkeypatch.setattr(core.pi_client, "run_turn", respond)
    if channel == "web":
        session = store.create_session(db, p, channel)
        response = core.turn(db, p, session.id, "补充经历")
    else:
        incoming = Event(
            platform="feishu",
            org_id=p.org_id,
            event_id="overlap",
            external_user_id="synthetic-user",
            external_chat_id="synthetic-chat",
            text="补充经历",
        )
        reply = actions.text(db, p, incoming, "synthetic-app")
        session = store.get_session(db, p, reply.session_id)
        response = reply.response
    after = employee.my_requests(p, db, "all")
    previews = [m for m in response.messages if isinstance(m, s.PreviewCard)]
    assert len(after.data) == len(before.data) + int(partial)
    assert len(previews) == int(partial)
    if partial:
        assert [i.after.capability.key for i in previews[0].items] == [
            "incident_support"
        ]
    notice = captured[0]
    assert "等原请求审完" in notice["message"]
    assert "撤回" in notice["message"] and "合并" in notice["message"]
    overlap = notice["overlaps"][0]
    assert overlap["request_id"] == str(submitted.id)
    assert (
        overlap["submitted_at"]
        == row.submitted_at.astimezone(ZoneInfo("Asia/Shanghai")).isoformat()
    )
    assert overlap["reviewers"] == [
        r.model_dump(mode="json") for r in submitted.reviewers
    ]
    assert overlap["capabilities"] == ["自动化测试"]
    assert (
        overlap["original_items"][0]["owner_statement"] == original["owner_statement"]
    )
    assert overlap["original_items"][0]["level"] == "routine"
    assert overlap["original_items"][0]["evidence_ids"] == [str(evidence_id)]
    assert [i["capability_key"] for i in overlap["original_items"]] == [
        "automated_testing",
        "frontend_ui",
    ]
    status = next(
        m
        for m in response.messages
        if isinstance(m, s.StatusCard) and m.id == submitted.id
    )
    assert [a.action for a in status.actions] == ["withdraw"]
    assert str(submitted.id) in [str(id_) for id_ in session.request_ids]
    assert next(r for r in after.data if r.id == submitted.id) == next(
        r for r in before.data if r.id == submitted.id
    )
    core.act(db, p, session.id, status.actions[0])
    merged = overlap["original_items"]
    merged[0]["statement"] += "；新的经历"
    cards: list[s.Message] = []
    result = tools.call(
        db, p, session, cards, "draft_update_request", {"items": merged}
    )
    assert result["status"] == "draft"
    assert result["items"][0]["after"]["level"] == "routine"
    assert "新的经历" in result["items"][0]["after"]["statement"]
    assert len(cards) == 1 and isinstance(cards[0], s.PreviewCard)


@pytest.mark.parametrize(
    "status", [RequestStatus.DRAFT, RequestStatus.WITHDRAWN, RequestStatus.APPROVED]
)
def test_non_pending_request_does_not_block(
    demo: tuple, db: Session, status: RequestStatus
) -> None:
    p = principal_for(db, demo[1]["E02"].org_id)
    draft = employee.save_draft(p, db, None, [ItemInput(**payload())], "web")
    row = db.get(ProfileChangeRequest, draft.id)
    assert row
    row.status = status
    db.add(row)
    db.commit()
    session = store.create_session(db, p)
    cards: list[s.Message] = []
    result = tools.call(
        db, p, session, cards, "draft_update_request", {"items": [payload()]}
    )
    assert result["status"] == "draft" and "overlaps" not in result
    assert len(cards) == 1 and isinstance(cards[0], s.PreviewCard)


def test_other_employee_pending_request_does_not_block(
    demo: tuple, db: Session
) -> None:
    p = principal_for(db, demo[1]["E02"].org_id)
    other = replace(p, employee_id=demo[1]["E03"].id)
    draft = employee.save_draft(other, db, None, [ItemInput(**payload())], "web")
    employee.submit(other, db, draft.id, draft.revision, "web")
    db.commit()
    session = store.create_session(db, p)
    cards: list[s.Message] = []
    result = tools.call(
        db, p, session, cards, "draft_update_request", {"items": [payload()]}
    )
    assert result["status"] == "draft" and "overlaps" not in result
    assert draft.id not in session.request_ids
    assert len(cards) == 1 and isinstance(cards[0], s.PreviewCard)


def test_overlap_cannot_read_employee_in_another_tenant(
    demo: tuple, db: Session
) -> None:
    p = principal_for(db, demo[1]["E02"].org_id)
    draft = employee.save_draft(p, db, None, [ItemInput(**payload())], "web")
    employee.submit(p, db, draft.id, draft.revision, "web")
    db.commit()
    session = store.create_session(db, p)
    cards: list[s.Message] = []
    with pytest.raises(HTTPException) as error:
        tools.call(
            db,
            replace(p, org_id=uuid4()),
            session,
            cards,
            "draft_update_request",
            {"items": [payload()]},
        )
    assert error.value.status_code == 404
    assert cards == [] and session.request_ids == []


def test_partially_published_request_only_blocks_and_restores_pending_items(
    demo: tuple, db: Session
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
    draft = employee.save_draft(
        p,
        db,
        None,
        [ItemInput(**payload()), ItemInput(**payload("model_training"))],
        "web",
    )
    submitted = employee.submit(p, db, draft.id, draft.revision, "web")
    assert submitted.status == "pending_review"
    assert submitted.items[1].decision == "approved"
    db.commit()
    session = store.create_session(db, p)
    cards: list[s.Message] = []
    result = tools.call(
        db, p, session, cards, "draft_update_request", {"items": [payload()]}
    )
    assert [i["capability_key"] for i in result["overlaps"][0]["original_items"]] == [
        "automated_testing"
    ]
    cards.clear()
    result = tools.call(
        db,
        p,
        session,
        cards,
        "draft_update_request",
        {"items": [payload("model_training", op="modify")]},
    )
    assert result["status"] == "draft" and "overlaps" not in result
    assert len(cards) == 1 and isinstance(cards[0], s.PreviewCard)
