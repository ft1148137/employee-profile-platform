"""M9 confirmation entry contracts, using a fresh synthetic tenant per test."""

import json
import secrets
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.db import engine
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.bot import core, store, tools
from app.domains.bot import schemas as s
from app.domains.bot.models import BotSession
from app.domains.capacity import assistant
from app.domains.capacity import demand as demands
from app.domains.capacity.calendar import loads
from app.domains.capacity.models import (
    CapacityApproval,
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacitySetting,
    DemandEventKind,
    DemandStatus,
)
from app.domains.capacity.settings import CapacityForbidden
from app.domains.im import actions as im_actions
from app.domains.im.adapters.base import Event
from app.domains.im.models import ImAction
from app.domains.im.rendering import render
from app.domains.inbox.models import InboxItem
from app.domains.planning import service
from app.domains.planning.models import PlanningSnapshot
from app.domains.planning.tools import (
    DemandIdInput,
    get_approval_status,
    read_coordination_context,
)
from tests.api.routes.test_m9_proposal import case as case
from tests.api.routes.test_m9_proposal import counts, plan, proposal_row, propose, rows
from tests.api.routes.test_m9_proposal import planning_setup as planning_setup
from tests.api.routes.test_mcp import call, initialize, token_for
from tests.domains.test_bot_core import enable
from tests.domains.test_im_gateway import principal
from tests.utils.pi import PiHarness


def click(
    db: Session,
    p: Principal,
    session: BotSession,
    card: s.CapacityCard,
    client: TestClient,
) -> s.CapacityCard:
    if session.channel == "web":
        from app.core.security import create_access_token

        response = client.post(
            f"/api/v1/bot/sessions/{session.id}/actions",
            params={"org_id": str(p.org_id)},
            headers={
                "Authorization": f"Bearer {create_access_token(p.user_id, expires_delta=timedelta(minutes=5))}"
            },
            json=card.actions[0].model_dump(mode="json"),
        )
        assert response.status_code == 200, response.text
        return s.CapacityCard.model_validate(response.json()["messages"][0])
    session.external_chat_id, session.bot_identity = "synthetic-chat", "synthetic-app"
    db.add(session)
    button = ImAction(
        token=secrets.token_urlsafe(16),
        org_id=p.org_id,
        platform="feishu",
        bot_identity=session.bot_identity,
        employee_id=p.employee_id,
        session_id=session.id,
        external_chat_id=session.external_chat_id,
        message_id=uuid4().hex,
        action=card.actions[0].model_dump(mode="json"),
        expires_at=utc_now() + timedelta(hours=1),
    )
    db.add(button)
    db.commit()
    event = Event(
        org_id=p.org_id,
        platform="feishu",
        event_id=uuid4().hex,
        external_chat_id=session.external_chat_id,
        message_id=button.message_id,
        token=button.token,
        private=True,
    )
    reply = im_actions.callback(db, p, event, session.bot_identity)
    assert isinstance(reply, im_actions.Reply)
    assert (
        im_actions.callback(db, p, event, session.bot_identity)
        .response.messages[0]
        .text
        == "已处理"
    )
    return reply.response.messages[0]


@pytest.mark.parametrize("action", ["follow_up", "withdraw"])
def test_mcp_can_release_pending_proposal_without_accepting(
    case: tuple, client: TestClient, db: Session, action: str
) -> None:
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    _, people, _, demand = case
    headers = initialize(
        client, token_for(db, people["E01"])[0], name="Synthetic C3 client"
    )
    result = call(
        client,
        headers,
        "respond_demand",
        {
            "demand_id": str(demand.id),
            "action": action,
            "revision": reply.json()["revision"],
            "text": "合成拒绝理由" if action == "follow_up" else None,
            **({"reason_code": "priority"} if action == "follow_up" else {}),
        },
    )
    assert not result.get("isError"), result
    db.expire_all()
    assert proposal_row(db, demand).status == (
        "failed" if action == "follow_up" else "withdrawn"
    )
    current = demands.get_demand(
        principal(db, demand.org_id, people["E01"].id), db, demand.id
    )
    assert current.status == ("submitted" if action == "follow_up" else "withdrawn")
    assert not rows(db, CapacityAssignment, demand)
    events = rows(db, CapacityDemandEvent, demand)
    (replied,) = [e for e in events if e.kind == DemandEventKind.REPLIED]
    assert replied.via == "web"
    target_kind = (
        DemandEventKind.PROPOSAL_FAILED
        if action == "follow_up"
        else DemandEventKind.WITHDRAWN
    )
    (event,) = [e for e in events if e.kind == target_kind]
    assert len(events) == 2 and event.via == "mcp"
    if action == "follow_up":
        assert proposal_row(db, demand).fail_reason == "rejected"
        decision = next(
            a for a in rows(db, CapacityApproval, demand) if a.party == "requester"
        )
        assert decision.reason == "合成拒绝理由" and decision.via == "mcp"
    load = loads(db, demand.org_id, [people["E04"].id], demand.window_start, 2)
    assert all(week.hold_percent == 0 for week in load[people["E04"].id])
    assert db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == demand.org_id,
            AuditEvent.action == "mcp.tool_called",
            AuditEvent.target_id == "respond_demand",
        )
    ).all()
    before = counts(db, demand)
    duplicate = call(
        client,
        headers,
        "respond_demand",
        {
            "demand_id": str(demand.id),
            "action": action,
            "revision": reply.json()["revision"],
            "text": "合成拒绝理由",
        },
    )
    assert duplicate.get("isError") and counts(db, demand) == before


@pytest.mark.parametrize("action", ["follow_up", "withdraw"])
def test_mcp_expired_reject_and_withdraw_commit_expiry(
    case: tuple, client: TestClient, db: Session, action: str
) -> None:
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    demand_rev = reply.json()["revision"]
    prop = reply.json()["proposal"]
    _, people, _, demand = case
    row = proposal_row(db, demand)
    row.hold_expires_at = utc_now() - timedelta(seconds=1)
    db.add(row)
    db.commit()

    before_events = {e.id for e in rows(db, CapacityDemandEvent, demand)}
    before_notices = {n.id for n in rows(db, InboxItem, demand)}
    before_approvals = len(rows(db, CapacityApproval, demand))

    unauthorized_headers = initialize(
        client, token_for(db, people["E02"])[0], name="Synthetic unauthorized client"
    )
    headers = initialize(
        client, token_for(db, people["E01"])[0], name="Synthetic C3 client"
    )
    args = {
        "demand_id": str(demand.id),
        "action": action,
        "revision": demand_rev,
        "text": "迟到的拒绝" if action == "follow_up" else None,
        **({"reason_code": "timing"} if action == "follow_up" else {}),
    }
    denied = call(client, unauthorized_headers, "respond_demand", args)
    assert denied.get("isError")
    assert json.loads(denied["content"][0]["text"])["status"] == 404

    with Session(engine) as verify_db:
        assert proposal_row(verify_db, demand).status == "pending"
        assert {
            e.id for e in rows(verify_db, CapacityDemandEvent, demand)
        } == before_events
        assert {n.id for n in rows(verify_db, InboxItem, demand)} == before_notices
        before_audits = {a.id for a in rows(verify_db, AuditEvent, demand)}

    result = call(client, headers, "respond_demand", args)
    assert result.get("isError")
    payload = json.loads(result["content"][0]["text"])
    assert payload["status"] == 409
    assert payload["detail"]["code"] == "proposal_expired"

    with Session(engine) as verify_db:
        updated = proposal_row(verify_db, demand)
        assert updated.status == "failed"
        assert updated.fail_reason == "expired"
        assert updated.revision == prop["revision"] + 1
        saved_demand = verify_db.get_one(CapacityDemand, demand.id)
        assert saved_demand.status == DemandStatus.SUBMITTED
        assert saved_demand.revision == demand_rev + 1
        assert saved_demand.committed_start_date is None
        assert saved_demand.committed_end_date is None
        assert not rows(verify_db, CapacityAssignment, demand)
        assert len(rows(verify_db, CapacityApproval, demand)) == before_approvals
        assert (
            loads(verify_db, demand.org_id, [people["E04"].id], demand.window_start, 2)[
                people["E04"].id
            ][0].hold_percent
            == 0
        )

        new_events = [
            e
            for e in rows(verify_db, CapacityDemandEvent, demand)
            if e.id not in before_events
        ]
        assert len(new_events) == 1
        assert new_events[0].kind == DemandEventKind.PROPOSAL_FAILED
        assert new_events[0].text == "方案已过期，需要重新规划"
        assert new_events[0].via == "mcp"

        new_notices = [
            n for n in rows(verify_db, InboxItem, demand) if n.id not in before_notices
        ]
        assert {n.message for n in new_notices} == {"方案已过期，需要重新规划"}
        assert {demand.requester_user_id, updated.proposed_by_user_id} <= {
            n.recipient_user_id for n in new_notices
        }

        new_audits = [
            a for a in rows(verify_db, AuditEvent, demand) if a.id not in before_audits
        ]
        tool_audits = [a for a in new_audits if a.action == "mcp.tool_called"]
        biz_audits = [a for a in new_audits if a.action != "mcp.tool_called"]
        assert len(tool_audits) == 1
        assert tool_audits[0].target_id == "respond_demand"
        assert len(biz_audits) == 1
        assert biz_audits[0].action == "capacity.demand_follow_up"
        assert biz_audits[0].details["fail_reason"] == "expired"
        assert biz_audits[0].details["via"] == "mcp"
        assert biz_audits[0].details["proposal_id"] == prop["id"]

        before_counts = counts(verify_db, demand)
        before_notice_ids = {n.id for n in rows(verify_db, InboxItem, demand)}
        before_audit_ids = {a.id for a in rows(verify_db, AuditEvent, demand)}

    replay = call(client, headers, "respond_demand", args)
    assert replay.get("isError")
    replay_payload = json.loads(replay["content"][0]["text"])
    assert replay_payload["status"] == 409
    assert replay_payload["detail"]["code"] == "proposal_decided"

    with Session(engine) as replay_db:
        assert counts(replay_db, demand) == before_counts
        assert {n.id for n in rows(replay_db, InboxItem, demand)} == before_notice_ids
        assert proposal_row(replay_db, demand).status == "failed"
        replay_audits = [
            a
            for a in rows(replay_db, AuditEvent, demand)
            if a.id not in before_audit_ids
        ]
        assert len(replay_audits) == 1
        assert replay_audits[0].action == "mcp.tool_called"
        assert replay_audits[0].target_id == "respond_demand"


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_human_confirms_proposal_then_acceptance(
    case: tuple,
    client: TestClient,
    db: Session,
    channel: str,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, people, _, row = case
    option = plan(client, case)
    b = principal(db, row.org_id, people["E05"].id)
    a = principal(db, row.org_id, people["E01"].id)
    snapshot = db.get(PlanningSnapshot, option["snapshot_id"])
    case_view = service.get_case(b, db, snapshot.case_id)
    enable(db, row.org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    session = store.create_session(db, b, channel=channel)
    before = counts(db, row), len(rows(db, AuditEvent, row))
    pi_service.replies = [
        {
            "calls": [
                {
                    "name": "prepare_plan_proposal",
                    "arguments": {
                        "case_id": str(case_view.id),
                        "option_id": option["id"],
                        "outside_basis": True,
                        "text": "合成部门条件",
                    },
                }
            ]
        },
        "请核对确认卡",
    ]
    turn = core.turn(db, b, session.id, "请准备方案提议")
    card = next(m for m in turn.messages if isinstance(m, s.CapacityCard))
    assert card.arguments["option_id"] == option["id"]
    assert "预占投入 72 小时" in card.content
    assert not demands.get_demand(b, db, row.id).proposal
    assert (counts(db, row), len(rows(db, AuditEvent, row))) == before
    text, actions = render(card)
    assert card.content in text and actions == [
        x.model_dump(mode="json") for x in card.actions
    ]
    confirmed = click(db, b, session, card, client)
    assert isinstance(confirmed, s.CapacityCard) and confirmed.status == "confirmed", (
        confirmed
    )
    db.expire_all()
    pending = demands.get_demand(a, db, row.id)
    assert pending.proposal.status == "pending"
    (approval,) = rows(db, CapacityApproval, row)
    (event,) = [
        event
        for event in rows(db, CapacityDemandEvent, row)
        if event.kind == DemandEventKind.REPLIED
    ]
    assert (
        approval.party == "resource_manager"
        and approval.user_id == b.user_id
        and approval.via == event.via == ("bot" if channel == "web" else "feishu")
    )
    assert (
        loads(db, row.org_id, [people["E04"].id], row.window_start, 2)[
            people["E04"].id
        ][0].hold_percent
        == 50
    )
    a_session = store.create_session(db, a, channel=channel)
    pi_service.replies = [
        {
            "calls": [
                {
                    "name": "prepare_demand_response",
                    "arguments": {
                        "demand_id": str(row.id),
                        "revision": pending.revision,
                        "action": "accept",
                    },
                }
            ]
        },
        "请确认接受方案",
    ]
    turn = core.turn(db, a, a_session.id, "准备接受方案")
    raw = json.dumps(turn.model_dump(mode="json"), ensure_ascii=False)
    for person in (people["E04"], people["E05"]):
        assert person.display_name not in raw and str(person.id) not in raw
    assert any(isinstance(m, s.CapacityCard) for m in turn.messages), turn
    card = next(m for m in turn.messages if isinstance(m, s.CapacityCard))
    for label in ("人数", "投入", "开始", "结束", "条件", "到期", "锁定并生成分配"):
        assert label in card.content
    assert card.content in render(card)[0]
    result = click(db, a, a_session, card, client)
    assert result.status == "confirmed", result
    assert proposal_row(db, row).status == "committed"
    assert len(rows(db, CapacityAssignment, row)) == 1


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("change", ["expired", "decided", "unexpected"])
def test_confirmation_rechecks_and_keeps_failure_boundary(
    case: tuple,
    client: TestClient,
    db: Session,
    channel: str,
    change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = propose(client, case)
    assert reply.status_code == 200
    _, people, _, row = case
    a = principal(db, row.org_id, people["E01"].id)
    session = store.create_session(db, a, channel=channel)
    cards: list[s.Message] = []
    args = {
        "demand_id": str(row.id),
        "revision": reply.json()["revision"],
        "action": "accept",
    }
    result = tools.call(db, a, session, cards, "prepare_demand_response", args)
    assert result["status"] == "pending"
    card = cards[0]
    store.append_message(
        db, session, "assistant", s.TurnResponse(messages=cards).model_dump(mode="json")
    )
    if change == "expired":
        held = proposal_row(db, row)
        held.hold_expires_at = utc_now() - timedelta(seconds=1)
        db.add(held)
    elif change == "decided":
        demands.respond_demand(
            a,
            db,
            row.id,
            "follow_up",
            args["revision"],
            "合成拒绝",
            reason_code="priority",
        )
    else:
        original = demands.respond_demand

        def crash(*args: object, **kwargs: object) -> object:
            original(*args, **kwargs)
            raise RuntimeError("synthetic failure after writes")

        monkeypatch.setitem(
            assistant.WRITES,
            "respond_demand",
            replace(assistant.WRITES["respond_demand"], handler=crash),
        )
    db.commit()
    before = counts(db, row)
    if change == "unexpected":
        with pytest.raises(RuntimeError, match="synthetic failure"):
            core.act(db, a, session.id, card.actions[0])
        db.rollback()
        assert proposal_row(db, row).status == "pending"
    else:
        result = core.act(db, a, session.id, card.actions[0]).messages[0]
        assert result.status == "failed"
        assert (
            "过期" in result.result
            if change == "expired"
            else "已处理" in result.result
        )
        assert proposal_row(db, row).status == "failed"
    assert not rows(db, CapacityAssignment, row)
    if change != "expired":
        assert counts(db, row) == before


def test_mcp_accept_guard_and_legacy_flow(
    case: tuple, client: TestClient, db: Session
) -> None:
    _, people, _, row = case
    headers = initialize(client, token_for(db, people["E01"])[0])
    reply = propose(client, case)
    before = counts(db, row)
    args = {
        "demand_id": str(row.id),
        "revision": reply.json()["revision"],
        "action": "accept",
    }
    result = call(client, headers, "respond_demand", args)
    assert result["isError"]
    assert "接受方案会锁定对方资源，请在网页或助手的确认卡里操作" in json.dumps(
        result, ensure_ascii=False
    )
    a = principal(db, row.org_id, people["E01"].id)
    with pytest.raises(CapacityForbidden):
        demands.respond_demand(a, db, row.id, "accept", args["revision"], via="mcp")
    db.rollback()
    assert counts(db, row) == before
    assert proposal_row(db, row).status == "pending"


@pytest.mark.parametrize(
    "failure", ["stale", "missing", "long", "forbidden", "foreign", "self"]
)
def test_mcp_rejection_preserves_authorization(
    case: tuple, client: TestClient, db: Session, failure: str
) -> None:
    reply = propose(client, case)
    _, people, _, row = case
    actor = "E01"
    if failure == "self":
        from app.domains.org.models import OrgMembership, RoleGrant

        member = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == row.org_id,
                OrgMembership.employee_id == people["E05"].id,
            )
        ).one()
        db.add(
            RoleGrant(
                org_id=row.org_id,
                membership_id=member.id,
                role="dept_manager",
                department_id=row.requesting_department_id,
            )
        )
        db.commit()
        actor = "E05"
    elif failure == "forbidden":
        actor = "E02"
    if failure == "foreign":
        from uuid import UUID

        import jwt

        from app.domains.org.models import Employee, OrgMembership

        user_id = UUID(
            jwt.decode(
                case[2]["foreign"]["Authorization"].split()[1],
                options={"verify_signature": False},
            )["sub"]
        )
        member = db.exec(
            select(OrgMembership).where(OrgMembership.user_id == user_id)
        ).one()
        person = Employee(org_id=member.org_id, display_name="合成外部员工")
        db.add(person)
        db.flush()
        member.employee_id = person.id
        db.add(member)
        db.add(
            CapacitySetting(
                org_id=member.org_id, enabled=True, updated_by_user_id=user_id
            )
        )
        db.commit()
        headers = initialize(client, token_for(db, person)[0])
    else:
        headers = initialize(client, token_for(db, people[actor])[0])
    args = {
        "demand_id": str(row.id),
        "action": "follow_up",
        "reason_code": "scope",
        "revision": reply.json()["revision"] - (failure == "stale"),
        "text": None
        if failure == "missing"
        else "合" * 501
        if failure == "long"
        else "合成理由",
    }
    try:
        before = counts(db, row)
        assert call(client, headers, "respond_demand", args)["isError"]
        assert counts(db, row) == before and proposal_row(db, row).status == "pending"
    finally:
        if failure == "foreign":
            db.rollback()
            db.delete(
                db.exec(
                    select(CapacitySetting).where(
                        CapacitySetting.org_id == member.org_id
                    )
                ).one()
            )
            db.commit()


def test_coordination_proposal_projection_and_private_reads(
    case: tuple, client: TestClient, db: Session
) -> None:
    reply = propose(client, case, text="合成条件")
    assert reply.status_code == 200
    _, people, _, row = case
    a = principal(db, row.org_id, people["E01"].id)
    b = principal(db, row.org_id, people["E05"].id)
    body = DemandIdInput(demand_id=row.id)
    for handler in (get_approval_status, read_coordination_context):
        result = handler(a, db, body)
        raw = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
        for person in (people["E04"], people["E05"]):
            assert person.display_name not in raw and str(person.id) not in raw
        view = result.root.get("demand", result.root)
        assert view["proposal"]["status"] == "pending"
        assert view["proposal"]["hold_expires_at"]
        assert view["proposal"]["approvals"][0]["decision"] == "approved"
        assert "没有读取对方答复正文里的条件" in view["reply_notice"]
        assert view["demand_url"] == f"/capacity?tab=outgoing&demand={row.id}"
        detailed = handler(b, db, body)
        assert str(people["E04"].id) in detailed._read_employee_ids
        assert "_read_employee_ids" not in detailed.model_dump_json()


def test_model_has_no_direct_commitment_tools(case: tuple, db: Session) -> None:
    _, people, _, row = case
    b = principal(db, row.org_id, people["E05"].id)
    session = store.create_session(db, b)
    names = {d.name for d in tools.definitions(b, db=db)}
    for name in (
        "accept_proposal",
        "create_proposal",
        "create_assignment",
        "reply_demand",
        "respond_demand",
    ):
        assert name not in names
        cards: list[s.Message] = []
        result = tools.call(db, b, session, cards, name, {})
        assert result.get("error") and not cards
    cards = []
    result = tools.call(
        db,
        b,
        session,
        cards,
        "prepare_demand_reply",
        {
            "demand_id": str(row.id),
            "revision": row.revision,
            "outcome": "can_do",
            "start_date": row.window_start.isoformat(),
            "end_date": row.window_end.isoformat(),
            "text": "合成方案",
            "option_id": str(row.id),
        },
    )
    assert result.get("error") and not cards


def test_mcp_reply_schema_and_legacy_accept(
    case: tuple, client: TestClient, db: Session
) -> None:
    from tests.api.routes.test_m6_capacity_mcp import listing

    _, people, _, row = case
    headers = initialize(client, token_for(db, people["E01"])[0])
    listed = listing(client, headers)
    assert "option_id" not in listed["reply_demand"]["inputSchema"]["properties"]
    assert (
        not {"create_proposal", "create_assignment", "withdraw_proposal"}
        & listed.keys()
    )
    b = principal(db, row.org_id, people["E05"].id)
    reply = demands.reply_demand(
        b,
        db,
        row.id,
        "can_do",
        "合成旧流程",
        row.revision,
        start_date=row.window_start,
        end_date=row.window_end,
    )
    db.commit()
    before = counts(db, row)
    rejected = call(
        client,
        headers,
        "reply_demand",
        {
            "demand_id": str(row.id),
            "revision": reply.revision,
            "outcome": "can_do",
            "text": "合成方案",
            "option_id": str(row.id),
        },
    )
    assert rejected.get("isError") and counts(db, row) == before
    result = call(
        client,
        headers,
        "respond_demand",
        {"demand_id": str(row.id), "revision": reply.revision, "action": "accept"},
    )
    assert not result.get("isError"), result
    assert result["structuredContent"]["status"] == "accepted"
    assert not rows(db, CapacityAssignment, row)


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_proposal_card_rechecks_snapshot_after_employee_locks(
    case: tuple,
    client: TestClient,
    db: Session,
    channel: str,
) -> None:
    _, people, _, row = case
    option = plan(client, case)
    b = principal(db, row.org_id, people["E05"].id)
    snapshot = db.get(PlanningSnapshot, option["snapshot_id"])
    session = store.create_session(db, b, channel=channel)
    cards: list[s.Message] = []
    result = tools.call(
        db,
        b,
        session,
        cards,
        "prepare_plan_proposal",
        {
            "case_id": str(snapshot.case_id),
            "option_id": option["id"],
            "text": "合成部门条件",
            "outside_basis": True,
        },
    )
    assert result["status"] == "pending"
    card = cards[0]
    store.append_message(
        db, session, "assistant", s.TurnResponse(messages=cards).model_dump(mode="json")
    )
    service.create_case(b, db, row.id)
    db.commit()
    before = counts(db, row), len(rows(db, AuditEvent, row))
    result = click(db, b, session, card, client)
    assert result.status == "failed" and "重新模拟" in result.result
    assert (counts(db, row), len(rows(db, AuditEvent, row))) == before
    assert demands.get_demand(b, db, row.id).proposal is None
