"""Personnel writes between tool reads and turn persistence retain provenance."""

import json
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.authz.policies import can_view_employee
from app.domains.bot import core, store
from app.domains.bot.models import BotMessage
from app.domains.model.models import ModelSetting
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import Department, RoleGrant
from app.domains.org.paths import department_paths
from app.domains.org.roster import single
from app.models import User
from tests.api.routes.test_m8_employee_session_visibility import (
    demo as demo,  # noqa: F401
)
from tests.api.routes.test_m8_session_scope import HIDDEN, _headers
from tests.domains.test_im_gateway import principal

pytest_plugins = ("tests.api.routes.test_m8_coordination_tools",)


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("change", ["transfer", "offboard"])
@pytest.mark.parametrize(
    "source", ["team", "profile", "coordination", "inherited", "joined"]
)
@pytest.mark.parametrize("entrypoint", ["history", "turn"])
def test_in_turn_visibility_loss_retains_data_scope(
    coordination: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu"],
    change: str,
    source: str,
    entrypoint: str,
) -> None:
    people, _, demand = coordination
    target = people["E09"] if source == "joined" else people["E04"]
    p = principal(db, demand.org_id, people["E05"].id)
    row = store.create_session(db, p, channel=channel)
    private = f"{target.display_name} 的合成个人负荷为 80%"
    states: list[object] = []
    grant_values = [
        g.model_dump()
        for g in db.exec(select(RoleGrant).where(RoleGrant.org_id == p.org_id)).all()
    ]
    paths = department_paths(
        db.exec(select(Department).where(Department.org_id == p.org_id)).all()
    )
    args = (
        single.Transfer(
            employee_id=target.id, department=paths[people["E10"].department_id]
        )
        if change == "transfer"
        else single.Offboard(employee_ids=[target.id])
    )

    def change_personnel(change_args: single.SingleChange) -> None:
        # A separate transaction commits after data reached the model, before save.
        with Session(db.get_bind()) as other:
            setting = other.get_one(ModelSetting, p.org_id)
            admin = get_org_principal(
                p.org_id, other.get_one(User, setting.updated_by_user_id), other
            )
            single.apply(other, admin, change_args)

    def fake_turn(_config: object, **kwargs: object) -> TurnResult:
        states.append(kwargs["session_state"])
        inherited = source == "inherited" and len(states) == 2
        final = len(states) == (3 if source == "inherited" else 2)
        if final:
            assert kwargs["session_state"] is None
            db.refresh(row)
            assert row.state_ciphertext is None
            return TurnResult(
                text="安全的新回复",
                stop_reason="stop",
                model_calls=1,
                tool_calls=0,
                session_state={"messages": []},
            )
        if inherited:
            assert private in str(kwargs["session_state"])
        else:
            if source == "joined":
                # Newly visible people must also be tracked after turn admission.
                change_personnel(
                    single.Transfer(
                        employee_id=target.id,
                        department=paths[people["E04"].department_id],
                    )
                )
            name, arguments = {
                "team": ("list_team_members", {}),
                "joined": ("list_team_members", {}),
                "inherited": ("list_team_members", {}),
                "profile": ("get_member_profile", {"employee_id": str(target.id)}),
                "coordination": (
                    "coordination",
                    {
                        "function": "inspect_capacity",
                        "arguments": {"demand_id": str(demand.id)},
                    },
                ),
            }[source]
            result = kwargs["on_tool_call"](name, arguments)
            assert "error" not in result
            assert str(target.id) in json.dumps(result)
        if source != "inherited" or inherited:
            change_personnel(args)
        return TurnResult(
            text=private,
            stop_reason="stop",
            model_calls=1,
            tool_calls=0 if inherited else 1,
            session_state={"messages": [{"private": private}]},
        )

    monkeypatch.setattr(core.pi_client, "run_turn", fake_turn)
    core.turn(db, p, row.id, "查看团队")
    if source == "inherited":
        assert private in str(store.list_messages(db, p, row.id))
        core.turn(db, p, row.id, "继续使用旧上下文，不调用工具")
    db.expire_all()
    current = principal(db, p.org_id, people["E05"].id)
    assert current == p
    assert not can_view_employee(current, target)
    assert [
        g.model_dump()
        for g in db.exec(select(RoleGrant).where(RoleGrant.org_id == p.org_id)).all()
    ] == grant_values
    headers = _headers(db, p.org_id, people["E05"].id)
    assert (
        client.get(
            f"/api/v1/orgs/{p.org_id}/employees/{target.id}", headers=headers
        ).status_code
        == 404
    )
    original = {
        m.id: m.ciphertext
        for m in db.exec(
            select(BotMessage).where(
                BotMessage.org_id == p.org_id, BotMessage.session_id == row.id
            )
        ).all()
    }
    if entrypoint == "turn":
        core.turn(db, current, row.id, "重新开始")
    history = client.get(
        f"/api/v1/bot/sessions/{row.id}/messages",
        params={"org_id": str(p.org_id)},
        headers=headers,
    )
    assert history.status_code == 200, history.text
    assert private not in history.text
    hidden_index = 3 if source == "inherited" else 1
    assert history.json()[hidden_index]["payload"] == {
        "messages": [{"type": "text", "text": HIDDEN}]
    }
    if entrypoint == "history":
        core.turn(db, current, row.id, "重新开始")
    assert len(states) == (3 if source == "inherited" else 2)
    for message_id, ciphertext in original.items():
        assert db.get_one(BotMessage, message_id).ciphertext == ciphertext
    assert "安全的新回复" in str(store.list_messages(db, current, row.id))
