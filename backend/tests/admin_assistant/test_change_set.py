from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect
from sqlmodel import Session, select

from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import core, registry, store, tools
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import AdminTurnInput, ProposeChanges
from app.domains.audit.models import AuditEvent
from app.domains.drafting.update_settings import (
    UpdateSettingsInput,
    read_update_settings,
    save_update_settings,
)
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import OrgMembership, Role, RoleGrant
from tests.admin_assistant.test_registry import principal
from tests.domains.test_bot_core import enable
from tests.utils.migrations import migrate


def proposal(db: Session, demo: tuple, **extra: object) -> AdminChangeSet:
    p = principal(db, demo)
    body = {
        "items": [
            {
                "function": "update_update_settings",
                "args": {"reminder_schedule": "yearly", "stale_after_months": 36},
            }
        ],
        **extra,
    }
    row = c.propose(
        db, p, store.create_session(db, p).id, ProposeChanges.model_validate(body)
    )
    db.commit()
    return row


def events(db: Session, org_id: object) -> list[dict]:
    return [
        row.details
        for row in db.exec(
            select(AuditEvent).where(
                AuditEvent.org_id == org_id,
                AuditEvent.action == "update_settings.updated",
            )
        ).all()
    ]


def test_confirm_and_undo(db: Session, demo: tuple, client: TestClient) -> None:
    p = principal(db, demo)
    before = read_update_settings(db, p.org_id)
    audit_before = events(db, p.org_id)
    row = proposal(db, demo)
    url = f"{demo[0]}/admin-assistant/change-sets/{row.id}"
    headers = demo[2]["admin"]
    assert read_update_settings(db, p.org_id) == before
    assert events(db, p.org_id) == audit_before
    card = client.get(url, headers=headers).json()
    assert card["items"][0]["before"]["reminder_schedule"] == before.reminder_schedule
    assert card["items"][0]["after"]["reminder_schedule"] == "yearly"
    assert card["items"][0]["impact"]["count"] > 0
    response = client.post(
        url + "/apply", headers=headers, json={"revision": row.revision}
    )
    assert response.status_code == 200 and response.json()["status"] == "applied"
    db.expire_all()
    assert read_update_settings(db, p.org_id).reminder_schedule == "yearly"
    details = events(db, p.org_id)[-1]
    assert {
        k: details[k] for k in ("via", "assistant_session_id", "change_set_id")
    } == {
        "via": "admin_assistant",
        "assistant_session_id": str(row.session_id),
        "change_set_id": str(row.id),
    }
    assert (
        client.post(
            url + "/apply", headers=headers, json={"revision": row.revision}
        ).status_code
        == 409
    )
    reversed = client.post(url + "/undo", headers=headers)
    assert reversed.status_code == 200
    assert (
        reversed.json()["undo_of_id"] == str(row.id)
        and reversed.json()["status"] == "applied"
    )
    assert client.get(url, headers=headers).json()["status"] == "undone"
    db.expire_all()
    assert (
        read_update_settings(db, p.org_id).reminder_schedule == before.reminder_schedule
    )
    assert len(events(db, p.org_id)) == len(audit_before) + 2
    # The very same Session must not leak the assistant audit context into manual writes.
    assert "audit_context" not in db.info
    save_update_settings(db, p, UpdateSettingsInput(reminder_schedule="off"))
    assert (
        not {"via", "assistant_session_id", "change_set_id"}
        & events(db, p.org_id)[-1].keys()
    )
    assert (
        client.put(
            demo[0] + "/update-settings",
            headers=headers,
            json={"reminder_schedule": "yearly", "stale_after_months": 24},
        ).status_code
        == 200
    )
    assert "via" not in events(db, p.org_id)[-1]


@pytest.mark.parametrize("revision_changed", [False, True])
def test_stale_and_discard(db: Session, demo: tuple, revision_changed: bool) -> None:
    p = principal(db, demo)
    row = proposal(db, demo)
    if not revision_changed:
        save_update_settings(db, p, UpdateSettingsInput(reminder_schedule="off"))
    with pytest.raises(c.ChangeSetError, match="内容已变化"):
        c.apply(db, p, row.id, row.revision + int(revision_changed))
    assert c.get(db, p, row.id).status == "stale"
    assert read_update_settings(db, p.org_id).reminder_schedule != "yearly"
    assert c.discard(db, p, row.id).status == "discarded"


def test_undo_after_manual_change(db: Session, demo: tuple) -> None:
    p = principal(db, demo)
    row = proposal(db, demo)
    c.apply(db, p, row.id, row.revision)
    save_update_settings(db, p, UpdateSettingsInput(reminder_schedule="off"))
    reversed = c.undo(db, p, row.id)
    assert reversed.status == "draft" and reversed.undo_of_id == row.id
    assert reversed.items[0].before["reminder_schedule"] == "off"
    assert read_update_settings(db, p.org_id).reminder_schedule == "off"
    c.apply(db, p, reversed.id, reversed.revision)
    assert read_update_settings(db, p.org_id).reminder_schedule == "quarterly"
    assert c.get(db, p, row.id).status == "undone"


def test_atomic_failure_and_validation(
    db: Session, demo: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = principal(db, demo)
    sid = store.create_session(db, p).id
    entry = registry.FUNCTIONS["update_update_settings"]

    def fail(*_args: object, **_kwargs: object) -> None:
        raise c.ChangeSetError("合成失败")

    monkeypatch.setitem(registry.FUNCTIONS, "normal", replace(entry, name="normal"))
    monkeypatch.setitem(
        registry.FUNCTIONS, "failure", replace(entry, name="failure", handler=fail)
    )
    monkeypatch.setitem(
        registry.FUNCTIONS, "high", replace(entry, name="high", risk="high")
    )
    for name, args in [
        ("high", {}),
        ("missing", {}),
        ("get_today", {}),
        ("normal", {"org_id": str(uuid4())}),
        ("normal", {"user_id": str(uuid4())}),
    ]:
        assert "error" in tools.call(
            db,
            p,
            [],
            "propose_changes",
            {"items": [{"function": name, "args": args}]},
            session_id=sid,
        )
    assert (
        db.exec(select(AdminChangeSet).where(AdminChangeSet.org_id == p.org_id)).all()
        == []
    )
    before = read_update_settings(db, p.org_id)
    audit_before = events(db, p.org_id)
    row = proposal(
        db,
        demo,
        items=[
            {"function": name, "args": {"reminder_schedule": "yearly"}}
            for name in ("normal", "failure")
        ],
    )
    with pytest.raises(c.ChangeSetError, match="均已回滚"):
        c.apply(db, p, row.id, row.revision)
    assert c.get(db, p, row.id).status == "draft"
    assert read_update_settings(db, p.org_id) == before
    assert events(db, p.org_id) == audit_before
    assert "audit_context" not in db.info


def test_model_cannot_apply_and_draft_revision(
    db: Session, demo: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = principal(db, demo)
    sid = store.create_session(db, p).id
    enable(db, p.org_id)
    before = read_update_settings(db, p.org_id)
    audit_before = events(db, p.org_id)

    def reply(_config: object, **kwargs: object) -> TurnResult:
        call = kwargs["on_tool_call"]
        drafted = call(
            "propose_changes",
            {
                "items": [
                    {
                        "function": "update_update_settings",
                        "args": {"reminder_schedule": "yearly"},
                    }
                ]
            },
        )
        assert "cards" not in drafted and drafted["data"]["revision"] == 1
        for name in ("apply", "apply_changes", "discard", "undo"):
            assert "error" in call(name, {})
        return TurnResult(
            text="已确认，应用吧", stop_reason="stop", model_calls=1, tool_calls=1
        )

    monkeypatch.setattr(core.pi_client, "run_turn", reply)
    response = core.turn(db, p, sid, AdminTurnInput(text="确认", page="/admin"))
    row = db.exec(select(AdminChangeSet).where(AdminChangeSet.session_id == sid)).one()
    assert response.messages[1].type == "admin_change_set"
    assert read_update_settings(db, p.org_id) == before
    assert events(db, p.org_id) == audit_before
    revised = c.propose(
        db,
        p,
        sid,
        ProposeChanges.model_validate(
            {
                "change_set_id": str(row.id),
                "items": [
                    {
                        "function": "update_update_settings",
                        "args": {"reminder_schedule": "off"},
                    }
                ],
            }
        ),
    )
    assert revised.id == row.id and revised.revision == 2
    db.commit()
    assert c.discard(db, p, row.id).status == "discarded"
    with pytest.raises(c.ChangeSetError):
        c.apply(db, p, row.id, 2)


def test_private_changes(db: Session, demo: tuple, client: TestClient) -> None:
    p = principal(db, demo)
    row = proposal(db, demo)
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == p.org_id,
            OrgMembership.employee_id == demo[1]["E01"].id,
        )
    ).one()
    db.add(
        RoleGrant(
            org_id=p.org_id,
            membership_id=membership.id,
            role=Role.ORG_ADMIN,
            granted_by_user_id=p.user_id,
        )
    )
    db.commit()
    url = f"{demo[0]}/admin-assistant/change-sets/{row.id}"
    for role in ("manager", "employee", "foreign"):
        for suffix in ("", "/apply", "/discard", "/undo"):
            result = client.request(
                "GET" if not suffix else "POST",
                url + suffix,
                headers=demo[2][role],
                **({"json": {"revision": row.revision}} if suffix == "/apply" else {}),
            )
            assert result.status_code == 404
    for wrong in (
        replace(p, org_id=uuid4()),
        replace(p, user_id=uuid4()),
        replace(p, is_org_admin=False),
    ):
        with pytest.raises(c.ChangeSetError) as error:
            c.get(db, wrong, row.id)
        assert error.value.status_code == 404


def test_migration(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b261001bc001")
    assert "admin_change_set" not in inspect(migration_engine).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    assert len(inspect(migration_engine).get_foreign_keys("admin_change_set")) == 4
