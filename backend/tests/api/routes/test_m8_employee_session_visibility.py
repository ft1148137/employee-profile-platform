"""Employee visibility changes must invalidate cached assistant disclosures."""

import json
from dataclasses import replace
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.authz.deps import get_org_principal
from app.authz.policies import can_view_employee
from app.core.secrets import encrypt_secret
from app.domains.bot import core, store
from app.domains.bot.models import BotMessage
from app.domains.model.models import ModelSetting
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import (
    Department,
    OrgMembership,
    RoleGrant,
)
from app.domains.org.paths import department_paths
from app.domains.org.roster import single
from app.models import User
from tests.api.routes.test_m8_session_scope import HIDDEN, _headers
from tests.domains.test_im_gateway import principal

pytest_plugins = ("tests.api.routes.test_m8_coordination_tools",)


@pytest.fixture
def demo(isolated_demo: tuple, db: Session) -> tuple:
    _, people, _ = isolated_demo
    org_id = people["E01"].org_id
    admin_user_id = db.exec(
        select(OrgMembership.user_id)
        .join(RoleGrant, col(RoleGrant.membership_id) == col(OrgMembership.id))
        .join(User, col(OrgMembership.user_id) == col(User.id))
        .where(
            OrgMembership.org_id == org_id,
            RoleGrant.org_id == org_id,
            RoleGrant.role == "org_admin",
            col(User.is_superuser).is_(False),
        )
    ).one()
    db.add(
        ModelSetting(
            org_id=org_id,
            updated_by_user_id=admin_user_id,
            enabled=True,
            kind="cloud_api",
            provider="openai-compatible",
            model="synthetic",
            base_url="http://127.0.0.1/v1",
        )
    )
    db.commit()
    return isolated_demo


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("change", ["transfer", "offboard"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("entrypoint", ["history", "turn"])
def test_employee_visibility_loss_clears_history_and_model_context(
    coordination: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu"],
    change: str,
    legacy: bool,
    entrypoint: str,
) -> None:
    people, auth, demand = coordination
    target = people["E04"]
    p = principal(db, demand.org_id, people["E05"].id)
    row = store.create_session(db, p, channel=channel)
    private = f"{target.display_name} 的合成个人负荷为 80%"
    states: list[object] = []

    def fake_turn(_config: object, **kwargs: object) -> TurnResult:
        states.append(kwargs["session_state"])
        if len(states) == 1:
            call = kwargs["on_tool_call"]
            for name, arguments in (
                ("list_team_members", {}),
                ("get_member_profile", {"employee_id": str(target.id)}),
                (
                    "coordination",
                    {
                        "function": "inspect_capacity",
                        "arguments": {"demand_id": str(demand.id)},
                    },
                ),
            ):
                result = call(name, arguments)
                assert "error" not in result
                if name != "get_member_profile":
                    assert target.display_name in json.dumps(result, ensure_ascii=False)
        else:
            assert kwargs["session_state"] is None
            db.refresh(row)
            assert row.state_ciphertext is None
            assert private not in str(kwargs["input"])
            assert private not in str(kwargs["system_prompt"])
        return TurnResult(
            text=private if len(states) == 1 else "新上下文回复",
            stop_reason="stop",
            model_calls=1,
            tool_calls=3 if len(states) == 1 else 0,
            session_state={
                "version": 1,
                "messages": [{"private": private}] if len(states) == 1 else [],
            },
        )

    monkeypatch.setattr(core.pi_client, "run_turn", fake_turn)
    core.turn(db, p, row.id, "查看我的团队")
    assert private in str(store.list_messages(db, p, row.id))
    if legacy:
        # Reproduce the deployed format, which has roles but no employee snapshot.
        saved = store.load_state(db, row, scoped=True)
        assert saved is not None and isinstance(saved["scope"], dict)
        saved["scope"].pop("visible_employee_ids", None)
        store.save_state(db, row, saved)
        assistant = db.exec(
            select(BotMessage).where(
                BotMessage.org_id == p.org_id,
                BotMessage.session_id == row.id,
                BotMessage.role == "assistant",
            )
        ).one()
        payload = store.messages_for(db, row)[1].payload
        assert isinstance(payload, dict) and isinstance(payload["scope"], dict)
        payload["scope"].pop("visible_employee_ids", None)
        assistant.ciphertext = encrypt_secret(json.dumps(payload, ensure_ascii=False))
        db.add(assistant)
        db.commit()

    peers = []
    for code in ("E10", "foreign"):
        viewer = principal(db, people[code].org_id, people[code].id)
        peer = store.create_session(db, viewer)
        store.append_message(
            db,
            peer,
            "assistant",
            {
                "messages": [{"type": "text", "text": "其他会话回复"}],
                "scope": store.session_scope(db, viewer),
            },
        )
        store.save_scoped_state(db, viewer, peer, {"messages": ["其他会话上下文"]})
        db.commit()
        peers.append((viewer, peer, peer.state_ciphertext))

    original = {
        message.id: message.ciphertext
        for message in db.exec(
            select(BotMessage).where(
                BotMessage.org_id == p.org_id, BotMessage.session_id == row.id
            )
        ).all()
    }
    grants = list(db.exec(select(RoleGrant).where(RoleGrant.org_id == p.org_id)).all())
    grant_values = [grant.model_dump() for grant in grants]
    paths = department_paths(
        db.exec(select(Department).where(Department.org_id == p.org_id)).all()
    )
    body = (
        {
            "kind": "transfer",
            "employee_id": str(target.id),
            "department": paths[people["E10"].department_id],
        }
        if change == "transfer"
        else {"kind": "offboard", "employee_ids": [str(target.id)]}
    )
    base = f"/api/v1/orgs/{p.org_id}"
    preview = client.post(
        f"{base}/roster/single-change", json=body, headers=auth["admin"]
    )
    assert preview.status_code == 200, preview.text
    result = client.post(
        f"{base}/roster/imports/{preview.json()['id']}/apply",
        json={"base_revision": preview.json()["base_revision"]},
        headers=auth["admin"],
    )
    assert result.status_code == 200, result.text
    db.expire_all()
    current = principal(db, p.org_id, people["E05"].id)
    assert current == p
    for grant in grants:
        db.refresh(grant)
    assert [grant.model_dump() for grant in grants] == grant_values
    assert not can_view_employee(current, target)
    headers = _headers(db, p.org_id, people["E05"].id)
    assert (
        client.get(f"{base}/employees/{target.id}", headers=headers).status_code == 404
    )
    assert store.get_session(db, current, row.id).id == row.id
    with pytest.raises(core.tools.TeamMemberNotFound):
        core.tools.call(
            db, current, row, [], "get_member_profile", {"employee_id": str(target.id)}
        )
    if entrypoint == "turn":
        core.turn(db, current, row.id, "继续")
    history = client.get(
        f"/api/v1/bot/sessions/{row.id}/messages",
        params={"org_id": str(p.org_id)},
        headers=headers,
    )
    assert history.status_code == 200, history.text
    assert history.json()[0]["payload"] == {"text": "查看我的团队"}
    assert history.json()[1]["payload"] == {
        "messages": [{"type": "text", "text": HIDDEN}]
    }
    assert private not in history.text
    if entrypoint == "history":
        core.turn(db, current, row.id, "继续")
    assert states == [None, None]
    assert (
        store.list_messages(db, current, row.id)[-1].payload["messages"][0]["text"]
        == "新上下文回复"
    )
    for message_id, ciphertext in original.items():
        assert db.get_one(BotMessage, message_id).ciphertext == ciphertext
    for viewer, peer, ciphertext in peers:
        db.refresh(peer)
        assert peer.state_ciphertext == ciphertext
        assert "其他会话回复" in str(store.list_messages(db, viewer, peer.id))
        assert store.state_for_turn(db, viewer, peer) == {
            "messages": ["其他会话上下文"]
        }
        assert (
            client.get(
                f"/api/v1/bot/sessions/{peer.id}/messages",
                params={"org_id": str(p.org_id)},
                headers=headers,
            ).status_code
            == 404
        )


@pytest.mark.parametrize(
    "change", ["title", "expand", "legacy_safe", "admin_transfer", "admin_offboard"]
)
def test_employee_changes_without_visibility_loss_keep_context(
    coordination: tuple, db: Session, change: str
) -> None:
    people, _, demand = coordination
    p = principal(db, demand.org_id, people["E05"].id)
    admin = replace(p, is_org_admin=True)
    viewer = admin if change.startswith("admin_") else p
    row = store.create_session(db, viewer)
    scope = store.session_scope(db, viewer)
    if change == "legacy_safe":
        scope.pop("visible_employee_ids")
    store.append_message(
        db,
        row,
        "assistant",
        {
            "messages": [{"type": "text", "text": "仍可读取的旧回复"}],
            "scope": scope,
        },
    )
    store.save_state(db, row, {"messages": ["仍可读取的上下文"], "scope": scope})
    db.commit()
    paths = department_paths(
        db.exec(select(Department).where(Department.org_id == p.org_id)).all()
    )
    target = people["E10"] if change == "expand" else people["E04"]
    args = (
        single.Offboard(employee_ids=[target.id])
        if change == "admin_offboard"
        else single.Transfer(
            employee_id=target.id, department=paths[people["E10"].department_id]
        )
        if change == "admin_transfer"
        else single.Transfer(
            employee_id=target.id, department=paths[people["E04"].department_id]
        )
        if change == "expand"
        else single.Transfer(employee_id=target.id, title="合成新职位")
    )
    admin_member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == p.org_id,
            col(OrgMembership.employee_id).is_(None),
        )
    ).first()
    assert admin_member is not None
    actual_admin = get_org_principal(
        p.org_id, db.get_one(User, admin_member.user_id), db
    )
    single.apply(db, actual_admin, args)
    db.expire_all()
    assert can_view_employee(viewer, target)
    assert "仍可读取的旧回复" in str(store.list_messages(db, viewer, row.id))
    assert store.state_for_turn(db, viewer, row) == {"messages": ["仍可读取的上下文"]}
