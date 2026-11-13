import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.db import engine
from app.core.security import create_access_token
from app.domains.bot import core, store, tools
from app.domains.bot import schemas as s
from app.domains.bot.models import BotSession
from app.domains.im import actions, notifications, rendering
from app.domains.im.models import ImAction, ImBinding, ImNotification, ImSetting
from app.domains.inbox.models import InboxItem
from app.domains.org.models import Department, OrgMembership, Role, RoleGrant
from app.domains.review import direct
from tests.api.routes.test_direct_profile_edits import edit_body
from tests.api.routes.test_reviews import actor
from tests.domains.test_bot_core import enable
from tests.domains.test_im_conversation import button
from tests.domains.test_im_gateway import event


def prepared(db: Session, demo: tuple, channel: str) -> tuple:
    subject = demo[1]["E02"]
    p = actor(db, subject.org_id, "manager.platform@demo.example.com")
    # A grant on the parent must include the subject's child department.
    parent = Department(org_id=p.org_id, name="合成上级部门")
    db.add(parent)
    db.flush()
    department = db.get(Department, subject.department_id)
    department.parent_department_id = parent.id
    db.add(department)
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == p.org_id, OrgMembership.user_id == p.user_id
        )
    ).one()
    grant = db.exec(
        select(RoleGrant).where(
            RoleGrant.org_id == p.org_id,
            RoleGrant.membership_id == membership.id,
            RoleGrant.role == Role.DEPT_MANAGER,
        )
    ).one()
    grant.department_id = parent.id
    db.add(grant)
    db.commit()
    p = actor(db, subject.org_id, "manager.platform@demo.example.com")
    assert subject.department_id in p.managed_department_ids
    session = store.create_session(db, p, channel, "100", "12345")
    args = {"employee_id": str(subject.id), **edit_body(db, subject)}
    cards: list[s.Message] = []
    tools.call(db, p, session, cards, "prepare_profile_edit", args)
    return subject, p, session, cards[0], args


def invoke(
    db: Session, client: TestClient, p: Principal, session: BotSession, action: dict
) -> int:
    if session.channel == "web":
        response = client.post(
            f"/api/v1/bot/sessions/{session.id}/actions",
            params={"org_id": str(p.org_id)},
            headers={
                "Authorization": f"Bearer {create_access_token(p.user_id, expires_delta=timedelta(minutes=5))}"
            },
            json=action,
        )
        return response.status_code
    try:
        core.act(db, p, session.id, TypeAdapter(s.Action).validate_python(action))
        return 200
    except HTTPException as error:
        return error.status_code
    except ValidationError:
        return 422


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_team_read_confirm_and_notify(
    demo: tuple, db: Session, client: TestClient, channel: str
) -> None:
    subject, p, session, card, args = prepared(db, demo, channel)
    members = tools.call(
        db, p, session, [], "list_team_members", {"q": subject.display_name}
    )
    assert members["data"][0]["employee_id"] == str(subject.id)
    assert set(members["data"][0]) == {"employee_id", "name", "department", "title"}
    profile = tools.call(
        db, p, session, [], "get_member_profile", {"employee_id": str(subject.id)}
    )
    assert profile["revision"] == args["base_revision"]
    assert "evidence" not in json.dumps(profile) and "source" not in json.dumps(profile)
    assert set(profile["items"][0]) == {"capability", "claim", "status"}
    assert profile["items"][0]["claim"]["level_name"] == "能独立完成复杂任务"
    assert card.items[0].after.level_name == "能完成常规任务"
    assert "routine" not in rendering.render(card)[0]
    assert "确认后立即发布" in rendering.render(card)[0]
    core._save(db, session, [card])
    db.add(
        ImBinding(
            org_id=p.org_id,
            employee_id=subject.id,
            platform="feishu",
            external_user_id="owner",
            external_chat_id="owner-chat",
            bot_identity="12345",
        )
    )
    db.add(
        ImSetting(
            org_id=p.org_id,
            platform="feishu",
            enabled=True,
            bot_identity="12345",
            credentials="synthetic",
            updated_by_user_id=p.user_id,
        )
    )
    db.commit()
    assert edit_body(db, subject)["base_revision"] == args["base_revision"]
    assert (
        invoke(db, client, p, session, card.actions[1].model_dump(mode="json")) == 200
    )
    assert edit_body(db, subject)["base_revision"] == args["base_revision"]
    if channel == "feishu":
        incoming = replace(event(p.org_id, "改画像"), platform="feishu")
        sent = []

        class FakePlatform:
            async def render(self, chat: str, text: str, buttons: list) -> str:
                sent.append((text, buttons))
                return "team-message"

        asyncio.run(
            actions.deliver(
                db,
                p,
                incoming,
                "12345",
                actions.Reply(session.id, s.TurnResponse(messages=[card])),
                FakePlatform(),
            )
        )
        row = db.exec(
            select(ImAction).where(
                ImAction.org_id == p.org_id,
                ImAction.action["action"].astext == "confirm_profile_edit",
            )
        ).one()
        assert sent[0][1][0][0] == "确认修改"
        callback = button(incoming, row)
        assert isinstance(actions.callback(db, p, callback, "12345"), actions.Reply)
        assert isinstance(actions.callback(db, p, callback, "12345"), actions.Reply)
    else:
        assert (
            invoke(db, client, p, session, card.actions[0].model_dump(mode="json"))
            == 200
        )
    db.expire_all()
    assert edit_body(db, subject)["base_revision"] == args["base_revision"] + 1
    profile = tools.call(
        db, p, session, [], "get_member_profile", {"employee_id": str(subject.id)}
    )
    claim = next(
        i["claim"] for i in profile["items"] if i["capability"]["key"] == "backend_api"
    )
    assert (
        claim["assessment_status"] == "confirmed"
        and claim["statement"] == "合成的直接修改"
    )
    item = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == p.org_id,
            InboxItem.kind == "profile_edited",
            InboxItem.recipient_employee_id == subject.id,
        )
    ).one()
    notice = db.exec(
        select(ImNotification).where(
            ImNotification.org_id == p.org_id,
            ImNotification.inbox_item_id == item.id,
        )
    ).one()
    sent_notices = []

    class NoticePlatform:
        async def send_text(self, chat: str, text: str) -> str:
            sent_notices.append(text)
            return "notice"

    asyncio.run(
        notifications.send_pending(
            engine, {(p.org_id, "feishu"): ("synthetic", NoticePlatform())}
        )
    )
    db.refresh(notice)
    assert notice.status == "sent" and item.message in sent_notices[0]
    assert (
        invoke(db, client, p, session, card.actions[0].model_dump(mode="json")) == 409
    )


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize(
    "invalid", ["outside", "admin_outside", "self", "tenant", "missing", "employee"]
)
def test_team_scope(demo: tuple, db: Session, channel: str, invalid: str) -> None:
    subject, p, session, card, args = prepared(db, demo, channel)
    if invalid == "self":
        args["employee_id"] = str(p.employee_id)
    elif invalid in ("outside", "admin_outside"):
        args["employee_id"] = str(demo[1]["E04"].id)
        p = replace(p, is_org_admin=invalid == "admin_outside")
    elif invalid == "tenant":
        p = replace(p, org_id=uuid4())
    elif invalid == "missing":
        args["employee_id"] = str(uuid4())
    else:
        p = actor(db, subject.org_id, "employee.platform@demo.example.com")
    for name, body in [
        ("get_member_profile", {"employee_id": args["employee_id"]}),
        ("prepare_profile_edit", args),
    ]:
        if invalid == "employee":
            assert tools.call(db, p, session, [], name, body) == {
                "error": "不允许调用此工具"
            }
        elif invalid == "self" and name == "get_member_profile":
            assert tools.call(db, p, session, [], name, body)
        else:
            with pytest.raises(HTTPException) as error:
                tools.call(db, p, session, [], name, body)
            assert error.value.status_code == (403 if invalid == "self" else 404)
    names = {d.name for d in tools.definitions(p)}
    assert ("prepare_profile_edit" in names) == bool(p.managed_department_ids)
    if invalid == "employee":
        assert tools.call(db, p, session, [], "list_team_members", {}) == {
            "error": "不允许调用此工具"
        }
    if invalid == "admin_outside":
        members = tools.call(db, p, session, [], "list_team_members", {})
        assert args["employee_id"] not in [r["employee_id"] for r in members["data"]]


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize(
    "invalid",
    [
        "unseen",
        "card_id",
        "tamper",
        "stale",
        "revoked",
        "tenant",
        "outside",
        "admin_outside",
        "self",
        "employee",
    ],
)
def test_team_action_guards(
    demo: tuple, db: Session, client: TestClient, channel: str, invalid: str
) -> None:
    subject, p, session, card, args = prepared(db, demo, channel)
    expected = 409
    if invalid in ("outside", "admin_outside"):
        card.employee_id = demo[1]["E04"].id
        expected = 404
        if invalid == "admin_outside":
            membership = db.exec(
                select(OrgMembership).where(
                    OrgMembership.org_id == p.org_id,
                    OrgMembership.user_id == p.user_id,
                )
            ).one()
            db.add(
                RoleGrant(
                    org_id=p.org_id, membership_id=membership.id, role=Role.ORG_ADMIN
                )
            )
            p = replace(p, is_org_admin=True)
    elif invalid == "self":
        card.employee_id = p.employee_id
        expected = 403
    elif invalid == "employee":
        p = actor(db, subject.org_id, "employee.platform@demo.example.com")
        session = store.create_session(db, p, channel, "100", "12345")
        expected = 404
    if invalid != "unseen":
        core._save(db, session, [card])
    action = card.actions[0].model_dump(mode="json")
    if invalid == "card_id":
        action["card_id"] = str(uuid4())
    elif invalid == "tamper":
        action["items"] = [{"statement": "篡改"}]
        expected = 422
    elif invalid == "stale":
        direct.direct_edit(p, db, subject.id, card.edit, via="web", client_name="other")
    elif invalid == "revoked":
        subject.department_id = demo[1]["E04"].department_id
        db.add(subject)
        expected = 404
    elif invalid == "tenant":
        p = replace(p, org_id=uuid4())
        expected = 404
    db.commit()
    assert invoke(db, client, p, session, action) == expected
    db.expire_all()
    assert edit_body(db, subject)["base_revision"] == args["base_revision"] + (
        invalid == "stale"
    )


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_text_and_model_cannot_publish(
    demo: tuple, db: Session, channel: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject, p, session, card, args = prepared(db, demo, channel)
    enable(db, p.org_id)

    def model(*_args: object, **kwargs: object) -> core.pi_client.TurnResult:
        assert kwargs["on_tool_call"]("confirm_profile_edit", args)["error"]
        kwargs["on_tool_call"]("prepare_profile_edit", args)
        return core.pi_client.TurnResult(
            text="已确认并修改", stop_reason="stop", model_calls=1, tool_calls=2
        )

    monkeypatch.setattr(core.pi_client, "run_turn", model)
    response = core.turn(db, p, session.id, "确认，立即修改")
    assert any(isinstance(c, s.ProfileEditCard) for c in response.messages)
    assert edit_body(db, subject)["base_revision"] == args["base_revision"]
    assert not db.exec(
        select(InboxItem).where(
            InboxItem.org_id == p.org_id,
            InboxItem.kind == "profile_edited",
        )
    ).all()


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_admin_manager_scope_rechecked_after_lock(
    demo: tuple,
    db: Session,
    client: TestClient,
    channel: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, p, session, card, args = prepared(db, demo, channel)
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == p.org_id,
            OrgMembership.user_id == p.user_id,
        )
    ).one()
    db.add(RoleGrant(org_id=p.org_id, membership_id=membership.id, role=Role.ORG_ADMIN))
    p = replace(p, is_org_admin=True)
    core._save(db, session, [card])
    db.commit()
    original_lock = direct._lock_organization
    outside_department = demo[1]["E04"].department_id

    def transfer(principal: Principal, session: Session) -> None:
        original_lock(principal, session)
        row = session.get(type(subject), subject.id)
        row.department_id = outside_department
        session.add(row)
        session.flush()

    monkeypatch.setattr(direct, "_lock_organization", transfer)
    assert (
        invoke(db, client, p, session, card.actions[0].model_dump(mode="json")) == 404
    )
    db.rollback()
    assert edit_body(db, subject)["base_revision"] == args["base_revision"]
