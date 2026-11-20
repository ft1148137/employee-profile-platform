"""Coordination prose must retain conservative assistant privacy invalidation."""

import asyncio
import json
from collections.abc import Generator
from datetime import date, timedelta
from typing import Literal
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app.core.security import create_access_token
from app.domains.base import utc_now
from app.domains.bot import core, delivery, store
from app.domains.bot.schemas import TextMessage, TurnResponse
from app.domains.capability.models import CapabilitySchemaVersion, SchemaStatus
from app.domains.capacity.models import (
    CapacityApproval,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityProposal,
    CapacityProposalLine,
    CapacitySetting,
    DemandEventKind,
    DemandOutcome,
    DemandStatus,
)
from app.domains.im import actions
from app.domains.im.adapters.base import Adapter, Event
from app.domains.model.models import ModelSetting
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.planning.models import PlanningCase
from app.domains.planning.tools import COORDINATION_TOOLS
from app.models import User
from tests.domains.test_im_gateway import principal
from tests.utils.org import delete_test_organization


def headers(user_id: UUID) -> dict[str, str]:
    token = create_access_token(user_id, expires_delta=timedelta(minutes=10))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def privacy_case(db: Session) -> Generator[tuple]:
    org = Organization(slug=f"privacy-{uuid4()}", name="合成协调隐私企业")
    db.add(org)
    db.flush()
    departments = [
        Department(org_id=org.id, name=name)
        for name in ("合成需求部", "合成资源部", "合成其他部")
    ]
    db.add_all(departments)
    db.flush()
    people = {}
    users = {}
    for key, name, department in (
        ("a", "合成需求经理", departments[0]),
        ("b", "合成答复经理乙", departments[1]),
        ("target", "合成员工甲", departments[1]),
    ):
        user = User(
            username=str(uuid4()),
            email=f"{uuid4()}@example.com",
            hashed_password="unused",
        )
        person = Employee(
            org_id=org.id,
            department_id=department.id,
            display_name=name,
            employee_code=key,
            work_email=user.email,
        )
        db.add_all([user, person])
        db.flush()
        membership = OrgMembership(
            org_id=org.id, user_id=user.id, employee_id=person.id
        )
        db.add(membership)
        db.flush()
        if key in {"a", "b"}:
            db.add(
                RoleGrant(
                    org_id=org.id,
                    membership_id=membership.id,
                    role=Role.DEPT_MANAGER,
                    department_id=department.id,
                )
            )
        users[key], people[key] = user, person
    db.add_all(
        [
            CapabilitySchemaVersion(
                org_id=org.id, version=1, status=SchemaStatus.ACTIVE
            ),
            CapacitySetting(
                org_id=org.id, enabled=True, updated_by_user_id=users["b"].id
            ),
            ModelSetting(
                org_id=org.id,
                enabled=True,
                kind="cloud_api",
                provider="openai-compatible",
                model="synthetic",
                base_url="http://127.0.0.1/v1",
                updated_by_user_id=users["b"].id,
            ),
        ]
    )
    demand = CapacityDemand(
        org_id=org.id,
        requesting_department_id=departments[0].id,
        requester_user_id=users["a"].id,
        target_department_id=departments[1].id,
        capability_key="synthetic",
        capability_name="合成能力",
        headcount=1,
        allocation_percent=30,
        window_start=date.today(),
        window_end=date.today() + timedelta(days=13),
        description="合成协作需求",
        status=DemandStatus.REPLIED,
    )
    db.add(demand)
    db.flush()
    private = f"{people['target'].display_name}只能在后半周投入30%"
    db.add(
        CapacityDemandEvent(
            org_id=org.id,
            demand_id=demand.id,
            kind=DemandEventKind.REPLIED,
            actor_user_id=users["b"].id,
            via="web",
            text=private,
            outcome=DemandOutcome.PARTIAL,
        )
    )
    db.commit()
    try:
        yield people, users, demand, departments[2], private
    finally:
        db.rollback()
        for model in (
            CapacityApproval,
            CapacityProposalLine,
            CapacityProposal,
            CapacityDemandEvent,
            CapacityDemand,
            CapacitySetting,
        ):
            db.exec(delete(model).where(model.org_id == org.id))
        delete_test_organization(db, org.id)
        for user in users.values():
            db.delete(user)
        db.commit()


@pytest.mark.parametrize(
    "tool_name", ["get_approval_status", "read_coordination_context"]
)
@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("change", ["transfer", "offboard"])
@pytest.mark.parametrize("continuation", [False, True])
def test_reply_prose_without_case_invalidates_inherited_context(
    privacy_case: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    channel: Literal["web", "feishu"],
    change: str,
    continuation: bool,
) -> None:
    people, users, demand, outside, private = privacy_case
    p = principal(db, demand.org_id, people["b"].id)
    assert not db.exec(
        select(PlanningCase).where(PlanningCase.org_id == p.org_id)
    ).all()
    chat, identity = "synthetic-chat", "synthetic-app"
    session = store.create_session(db, p, channel, chat, identity)
    org_id, demand_id, session_id = demand.org_id, demand.id, session.id
    target_id, outside_id = people["target"].id, outside.id
    session_url = f"/api/v1/bot/sessions/{session_id}"
    auth = headers(users["b"].id)
    states: list[object] = []

    def model(_config: object, **kwargs: object) -> TurnResult:
        states.append(kwargs["session_state"])
        if len(states) == 1:
            result = kwargs["on_tool_call"](
                "coordination",
                {
                    "function": tool_name,
                    "arguments": {"demand_id": str(demand_id)},
                },
            )
            assert "error" not in result
            assert private in json.dumps(result, ensure_ascii=False)
            assert "_read_employee_ids" not in json.dumps(result)
        else:
            assert kwargs["session_state"] is not None
        return TurnResult(
            text=private,
            stop_reason="stop",
            model_calls=1,
            tool_calls=1 if len(states) == 1 else 0,
            session_state={"messages": [private]},
        )

    original_turn = core.turn

    def turn(turn_db: Session, *args: object, **kwargs: object) -> TurnResponse:
        result = original_turn(turn_db, *args, **kwargs)
        if len(states) == (2 if continuation else 1):
            target = turn_db.exec(
                select(Employee).where(
                    Employee.org_id == org_id, Employee.id == target_id
                )
            ).one()
            if change == "transfer":
                target.department_id = outside_id
            else:
                target.employment_status = EmploymentStatus.INACTIVE
            turn_db.add(target)
            turn_db.commit()
        return result

    def ask(question: str) -> str:
        if channel == "web":
            response = client.post(
                f"{session_url}/turns",
                params={"org_id": str(org_id)},
                headers=auth,
                json={"text": question},
            )
            assert response.status_code == 200, response.text
            events = [json.loads(line) for line in response.text.splitlines()]
            assert events[-1]["type"] == "final", events
            return json.dumps(events, ensure_ascii=False)
        event = Event(org_id, "feishu", uuid4().hex, "user", chat, True, question)
        reply = actions.text(db, p, event, identity)
        assert reply.session_id == session_id
        adapter = create_autospec(Adapter, instance=True)
        adapter.render.return_value = "sent"
        asyncio.run(actions.deliver(db, p, event, identity, reply, adapter))
        adapter.render.assert_awaited_once()
        return adapter.render.call_args.args[1]

    monkeypatch.setattr(core.pi_client, "run_turn", model)
    monkeypatch.setattr(core, "turn", turn)
    sent = ask("查看协调进度")
    if continuation:
        assert private in sent
        sent = ask("沿用旧上下文，不调用工具")
    db.expire_all()
    current = principal(db, demand.org_id, people["b"].id)
    response = client.get(
        f"{session_url}/messages", params={"org_id": str(org_id)}, headers=auth
    )
    assert response.status_code == 200, response.text
    history = response.text
    assert private not in history
    assert store.HIDDEN_REPLY in history
    assert private not in sent
    assert delivery.SCOPE_CHANGED in sent
    assert "_scope" not in sent and "read_employee_ids" not in sent
    assert store.state_for_turn(db, current, session) is None
    db.refresh(session)
    assert session.state_ciphertext is None


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("surface", ["history", "continuation", "delivery"])
@pytest.mark.parametrize("old_scope", ["partial", "full_unchanged", "full_narrowed"])
def test_upgrade_safeguards_saved_coordination_context(
    privacy_case: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu"],
    surface: str,
    old_scope: str,
) -> None:
    people, users, demand, outside, private = privacy_case
    p = principal(db, demand.org_id, people["b"].id)
    session = store.create_session(db, p, channel)
    text = TextMessage(text=private)
    scope = store.session_scope(db, p)
    if old_scope == "partial":
        # S3 recorded only the responder, including on turns inheriting this context.
        del scope["visible_employee_ids"]
        scope["read_employee_ids"] = [str(people["b"].id)]
    for steps in ([{"name": "调查并模拟部门协调方案", "ok": True}], []):
        store.append_message(
            db,
            session,
            "assistant",
            {
                "messages": [text.model_dump(mode="json")],
                "steps": steps,
                "scope": scope,
            },
        )
    old_state = {
        "messages": [
            {"role": "toolResult", "toolName": "coordination", "content": private},
            {"role": "assistant", "content": private},
        ]
    }
    store.save_scoped_state(db, p, session, old_state, scope=scope)
    hidden = old_scope != "full_unchanged"
    if hidden:
        people["target"].department_id = outside.id
        db.add(people["target"])
    db.commit()

    if surface == "history":
        response = client.get(
            f"/api/v1/bot/sessions/{session.id}/messages",
            params={"org_id": str(p.org_id)},
            headers=headers(users["b"].id),
        )
        assert response.status_code == 200, response.text
        assert (private in response.text) is not hidden
        if hidden:
            assert response.text.count(store.HIDDEN_REPLY) == 2
    elif surface == "delivery":
        pending = TurnResponse(messages=[text])
        pending._principal, pending._scope = p, scope
        with delivery.guard(db, pending) as protected:
            assert protected.messages == [
                TextMessage(text=delivery.SCOPE_CHANGED) if hidden else text
            ]
    else:
        states: list[object] = []

        def model(_config: object, **kwargs: object) -> TurnResult:
            states.append(kwargs["session_state"])
            if hidden:
                db.refresh(session)
                assert session.state_ciphertext is None
            return TurnResult(
                text="新回复", stop_reason="stop", model_calls=1, tool_calls=0
            )

        monkeypatch.setattr(core.pi_client, "run_turn", model)
        core.turn(db, p, session.id, "继续上一轮")
        assert states == [None if hidden else old_state]


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_upgrade_preserves_precise_scope_for_new_structured_reads(
    privacy_case: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu"],
) -> None:
    people, _, demand, outside, _ = privacy_case
    p = principal(db, demand.org_id, people["b"].id)
    reply = db.exec(
        select(CapacityDemandEvent).where(
            CapacityDemandEvent.org_id == p.org_id,
            CapacityDemandEvent.demand_id == demand.id,
        )
    ).one()
    reply.text = ""
    db.add(reply)
    db.commit()
    session = store.create_session(db, p, channel)
    text = "结构化答复"
    state = {"messages": [text]}

    def model(_config: object, **kwargs: object) -> TurnResult:
        result = kwargs["on_tool_call"](
            "coordination",
            {
                "function": "get_approval_status",
                "arguments": {"demand_id": str(demand.id)},
            },
        )
        assert result["events"][0]["responder_name"] == people["b"].display_name
        return TurnResult(
            text=text,
            stop_reason="stop",
            model_calls=1,
            tool_calls=1,
            session_state=state,
        )

    monkeypatch.setattr(core.pi_client, "run_turn", model)
    pending = core.turn(db, p, session.id, "查看审批")
    message = store.messages_for(db, session)[-1]
    saved = store.load_state(db, session, scoped=True)
    for scope in (message.payload["scope"], saved["scope"]):
        assert scope["read_scope_format"] == store.READ_SCOPE_FORMAT
        assert scope["read_employee_ids"] == [str(people["b"].id)]
        assert "visible_employee_ids" not in scope
    people["target"].department_id = outside.id
    db.add(people["target"])
    db.commit()

    assert text in str(store.list_messages(db, p, session.id))
    assert "read_scope_format" not in str(store.list_messages(db, p, session.id))
    assert store.state_for_turn(db, p, session) == state
    with delivery.guard(db, pending) as protected:
        assert protected.messages == [TextMessage(text=text)]


@pytest.fixture
def pending_proposal(privacy_case: tuple, db: Session) -> CapacityProposal:
    people, users, demand, _, _ = privacy_case
    proposal = CapacityProposal(
        org_id=demand.org_id,
        demand_id=demand.id,
        source_option_id=uuid4(),
        demand_revision=demand.revision,
        proposed_by_user_id=users["b"].id,
        hold_expires_at=utc_now() + timedelta(hours=72),
    )
    db.add(proposal)
    db.flush()
    db.add(
        CapacityProposalLine(
            org_id=demand.org_id,
            proposal_id=proposal.id,
            employee_id=people["target"].id,
            allocation_percent=30,
            start_date=demand.window_start,
            end_date=demand.window_end,
        )
    )
    db.commit()
    return proposal


@pytest.mark.parametrize(
    "tool_name", ["get_approval_status", "read_coordination_context"]
)
def test_pending_proposal_cannot_reintroduce_hidden_reply(
    privacy_case: tuple,
    pending_proposal: CapacityProposal,
    db: Session,
    client: TestClient,
    tool_name: str,
) -> None:
    people, users, demand, _, private = privacy_case
    tool = next(t for t in COORDINATION_TOOLS if t.name == tool_name)
    for role in ("a", "b"):
        p = principal(db, demand.org_id, people[role].id)
        result = tool.handler(
            p, db, tool.input_model.model_validate({"demand_id": demand.id})
        ).model_dump(mode="json")
        detail = (
            result["demand"] if tool_name == "read_coordination_context" else result
        )
        raw = json.dumps(result, ensure_ascii=False)
        assert "_read_employee_ids" not in raw and "_reads_complete" not in raw
        assert detail["proposal"]["id"] == str(pending_proposal.id)
        assert detail["proposal"]["status"] == "pending"
        if role == "a":
            assert private not in raw
            for key in ("b", "target"):
                assert people[key].display_name not in raw
                assert str(people[key].id) not in raw
            assert detail["reply_notice"] == ("完整条件请前往需求详情查看")
            assert detail["demand_url"] == f"/capacity?tab=outgoing&demand={demand.id}"
        else:
            assert private in raw
        response = client.get(
            f"/api/v1/orgs/{demand.org_id}/capacity/demands/{demand.id}",
            headers=headers(users[role].id),
        )
        assert response.status_code == 200, response.text
        assert response.json()["proposal"]["conditions"] == private


@pytest.mark.parametrize("with_reason", [False, True])
def test_proposal_reads_track_each_returned_employee_when_complete(
    privacy_case: tuple,
    pending_proposal: CapacityProposal,
    db: Session,
    with_reason: bool,
) -> None:
    people, users, demand, _, private = privacy_case
    p = principal(db, demand.org_id, people["b"].id)
    reply = db.exec(
        select(CapacityDemandEvent).where(
            CapacityDemandEvent.org_id == p.org_id,
            CapacityDemandEvent.demand_id == demand.id,
        )
    ).one()
    reply.text = ""
    other, unread = (
        Employee(
            org_id=p.org_id,
            department_id=demand.target_department_id,
            display_name=name,
        )
        for name in ("合成提议员工丙", "合成未读取员工丁")
    )
    db.add_all([reply, other, unread])
    db.flush()
    middle = demand.window_start + timedelta(days=7)
    first_line = db.exec(
        select(CapacityProposalLine).where(
            CapacityProposalLine.org_id == p.org_id,
            CapacityProposalLine.proposal_id == pending_proposal.id,
        )
    ).one()
    first_line.end_date = middle - timedelta(days=1)
    db.add(first_line)
    for person in (people["target"], other):
        db.add(
            CapacityProposalLine(
                org_id=p.org_id,
                proposal_id=pending_proposal.id,
                employee_id=person.id,
                allocation_percent=30,
                start_date=middle,
                end_date=demand.window_end,
            )
        )
    if with_reason:
        db.add(
            CapacityApproval(
                org_id=p.org_id,
                proposal_id=pending_proposal.id,
                user_id=users["b"].id,
                party="resource_manager",
                decision="approved",
                reason=private,
                via="web",
            )
        )
    db.commit()
    session = store.create_session(db, p)
    reports: list[set[str]] = []
    result = core.tools.call(
        db,
        p,
        session,
        [],
        "coordination",
        {"function": "get_approval_status", "arguments": {"demand_id": str(demand.id)}},
        on_read=reports.append,
    )
    ids = [line["employee_id"] for line in result["proposal"]["lines"]]
    assert sorted(ids) == sorted([str(people["target"].id)] * 2 + [str(other.id)])
    if with_reason:
        assert result["proposal"]["approvals"][0]["reason"] == private
        assert reports == []
    else:
        assert reports == [set(ids) | {str(people["b"].id)}]
    raw = json.dumps(result)
    assert "read_employee_ids" not in raw and "_reads_complete" not in raw
