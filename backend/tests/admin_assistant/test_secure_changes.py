import json
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, RootModel
from sqlalchemy import Engine, inspect
from sqlmodel import Session, select

from app.core.secrets import decrypt_secret
from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import core, registry, store, tools
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import AdminTurnInput, ProposeChanges
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.bot import store as messages
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import OrgMembership, Role, RoleGrant
from app.domains.review.policy import PolicyInput, policy_view, read_policy, save_policy
from tests.admin_assistant.test_registry import principal
from tests.domains.test_bot_core import enable
from tests.utils.migrations import migrate


def draft(db: Session, demo: tuple, function: str, args: dict) -> AdminChangeSet:
    p = principal(db, demo)
    row = c.propose(
        db,
        p,
        store.create_session(db, p).id,
        ProposeChanges.model_validate(
            {"items": [{"function": function, "args": args}]}
        ),
    )
    db.commit()
    return row


@pytest.mark.parametrize("category_only", [False, True])
def test_double_confirmation_and_expiry(
    db: Session, demo: tuple, client: TestClient, category_only: bool
) -> None:
    p = principal(db, demo)
    args = (
        {"category_overrides": {policy_view(db, p.org_id).categories[0]: False}}
        if category_only
        else {"require_manager": False}
    )
    row = draft(db, demo, "update_approval_policy", args)
    url = f"{demo[0]}/admin-assistant/change-sets/{row.id}"

    def apply() -> dict:
        result = client.post(
            url + "/apply", headers=demo[2]["admin"], json={"revision": row.revision}
        )
        assert result.status_code == 200, result.text
        return result.json()

    first = apply()
    assert first["status"] == "draft" and first["confirmed_once_at"]
    assert first["items"][0]["impact"]["count"] > 0
    assert "无需经理确认" in first["items"][0]["impact"]["text"]
    db.expire_all()
    assert read_policy(db, p.org_id).version == 0
    row.confirmed_once_at = utc_now() - timedelta(minutes=5, seconds=1)
    db.add(row)
    db.commit()
    assert apply()["status"] == "draft"
    db.expire_all()
    assert read_policy(db, p.org_id).version == 0
    assert apply()["status"] == "applied"
    db.expire_all()
    audit = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == p.org_id, AuditEvent.action == "approval_policy.saved"
        )
    ).one()
    assert audit.details["via"] == "admin_assistant"
    # The reverse enables confirmation and needs only one click.
    reverse = c.undo(db, p, row.id)
    assert reverse.status == "applied" and reverse.items[0].risk == "normal"
    assert read_policy(db, p.org_id).require_manager
    save_policy(db, p, PolicyInput())
    manual = db.exec(
        select(AuditEvent)
        .where(
            AuditEvent.org_id == p.org_id, AuditEvent.action == "approval_policy.saved"
        )
        .order_by(AuditEvent.created_at.desc())
    ).first()
    assert not {"via", "assistant_session_id", "change_set_id"} & manual.details.keys()


def test_high_risk_undo_survives_reload_and_revision_resets_confirmation(
    db: Session, demo: tuple
) -> None:
    p = principal(db, demo)
    save_policy(db, p, PolicyInput(require_manager=False))
    row = draft(db, demo, "update_approval_policy", {"require_manager": True})
    assert c.apply(db, p, row.id, row.revision).status == "applied"
    reverse = c.undo(db, p, row.id)
    assert reverse.status == "draft" and reverse.confirmed_once_at
    assert read_policy(db, p.org_id).require_manager
    assert messages.messages_for(db, store.get_session(db, p, row.session_id))[
        -1
    ].payload["messages"][0]["change_set"]["id"] == str(reverse.id)
    changed = c.propose(
        db,
        p,
        row.session_id,
        ProposeChanges.model_validate(
            {
                "change_set_id": str(reverse.id),
                "items": [
                    {
                        "function": "update_approval_policy",
                        "args": {"require_manager": False, "owner_review_days": 30},
                    }
                ],
            }
        ),
    )
    db.commit()
    assert changed.confirmed_once_at is None
    assert c.apply(db, p, changed.id, changed.revision).status == "draft"
    assert c.apply(db, p, changed.id, changed.revision).status == "applied"
    assert not read_policy(db, p.org_id).require_manager


@pytest.fixture
def secret_function(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    class Args(registry.NoArgs):
        api_key: str

    received: list[str] = []

    def handler(
        _db: Session, _p: object, args: Args, *, commit: bool = True
    ) -> BaseModel:
        assert not commit
        received.append(args.api_key)
        return registry.NoArgs()

    monkeypatch.setitem(
        registry.FUNCTIONS,
        "test_secret",
        replace(
            registry.FUNCTIONS["update_update_settings"],
            name="test_secret",
            input_model=Args,
            secret_fields=("api_key",),
            snapshot=None,
            impact=None,
            handler=handler,
        ),
    )
    return received


@pytest.mark.parametrize("finish", ["apply", "discard", "stale", "revise"])
def test_secret_storage_and_no_disclosure(
    db: Session,
    demo: tuple,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    secret_function: list[str],
    finish: str,
) -> None:
    p = principal(db, demo)
    row = draft(db, demo, "test_secret", {})
    url = f"{demo[0]}/admin-assistant/change-sets/{row.id}"
    headers = demo[2]["admin"]
    canary = "sk-synthetic-secret-canary-274"
    sid = row.session_id
    enable(db, p.org_id)

    def reply(_config: object, **kwargs: object) -> TurnResult:
        assert canary not in str(kwargs)
        result = kwargs["on_tool_call"](
            "request_secret_input", {"function": "test_secret", "field": "api_key"}
        )
        assert canary not in str(result)
        return TurnResult(
            text="请在安全输入框填写",
            stop_reason="stop",
            model_calls=1,
            tool_calls=1,
            session_state={
                "version": 1,
                "messages": [
                    {"role": "user", "content": kwargs["input"], "timestamp": 0},
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": str(result)}],
                        "timestamp": 0,
                    },
                ],
            },
        )

    monkeypatch.setattr(core.pi_client, "run_turn", reply)
    for _ in range(2):
        response = core.turn(
            db, p, sid, AdminTurnInput(text="填写连接密钥", page="/admin")
        )
        assert response.messages[1].type == "secret_input"
        saved = client.put(
            url + "/secrets/api_key", headers=headers, json={"value": canary}
        )
        assert saved.status_code == 200 and saved.json()["text"] == "已填写"
        assert canary not in saved.text
        db.expire_all()
        assert row.secrets and canary not in row.secrets
        assert json.loads(decrypt_secret(row.secrets)) == {"api_key": canary}
    for endpoint in (
        url,
        f"/api/v1/admin-assistant/sessions/{sid}/messages?org_id={p.org_id}",
        f"{demo[0]}/audit-events",
    ):
        response = client.get(endpoint, headers=headers)
        assert response.status_code == 200 and canary not in response.text
    session = store.get_session(db, p, sid)
    assert session.state_ciphertext and canary not in decrypt_secret(
        session.state_ciphertext
    )
    assert canary not in str(messages.messages_for(db, session))
    assert canary not in str(
        [
            e.details
            for e in db.exec(
                select(AuditEvent).where(AuditEvent.org_id == p.org_id)
            ).all()
        ]
    )
    if finish == "revise":
        c.propose(
            db,
            p,
            sid,
            ProposeChanges.model_validate(
                {
                    "change_set_id": str(row.id),
                    "items": [{"function": "test_secret", "args": {}}],
                }
            ),
        )
        db.commit()
    elif finish == "stale":
        with pytest.raises(c.ChangeSetError):
            c.apply(db, p, row.id, row.revision + 1)
    else:
        result = client.post(
            url + f"/{finish}", headers=headers, json={"revision": row.revision}
        )
        assert result.status_code == 200 and canary not in result.text
    db.expire_all()
    assert row.secrets is None
    assert secret_function == ([canary] if finish == "apply" else [])


def test_secret_validation_and_private_access(
    db: Session, demo: tuple, client: TestClient, secret_function: list[str]
) -> None:
    assert secret_function == []
    p = principal(db, demo)
    sid = store.create_session(db, p).id
    for function, field in (
        ("test_secret", "api_key"),
        ("missing", "api_key"),
        ("test_secret", "other"),
    ):
        assert (
            "error"
            in tools.call(
                db,
                p,
                [],
                "request_secret_input",
                {"function": function, "field": field},
                session_id=sid,
            )["data"]
        )
    row = draft(db, demo, "test_secret", {})
    url = f"{demo[0]}/admin-assistant/change-sets/{row.id}"
    headers = demo[2]["admin"]
    assert (
        client.post(
            url + "/apply", headers=headers, json={"revision": row.revision}
        ).status_code
        == 422
    )
    for field, body in (
        ("other", {"value": "canary"}),
        ("api_key", {"value": ""}),
        ("api_key", {"value": 123}),
        ("api_key", {"value": "canary", "extra": "canary"}),
    ):
        result = client.put(url + f"/secrets/{field}", headers=headers, json=body)
        assert result.status_code == 422 and "canary" not in result.text
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
    for role in ("manager", "employee", "foreign"):
        assert (
            client.put(
                url + "/secrets/api_key",
                headers=demo[2][role],
                json={"value": "canary"},
            ).status_code
            == 404
        )
    assert "error" in tools.call(
        db,
        p,
        [],
        "propose_changes",
        {"items": [{"function": "test_secret", "args": {"api_key": "canary"}}]},
        session_id=row.session_id,
    )
    c.discard(db, p, row.id)
    assert (
        client.put(
            url + "/secrets/api_key", headers=headers, json={"value": "canary"}
        ).status_code
        == 409
    )


@pytest.mark.parametrize("canary", ["sk-pasted-secret-canary-274", "aB12" * 9])
def test_pasted_secret_never_saved_or_sent(
    db: Session,
    demo: tuple,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    canary: str,
) -> None:
    p = principal(db, demo)
    sid = store.create_session(db, p).id
    enable(db, p.org_id)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("pasted secret reached model")

    monkeypatch.setattr(core.pi_client, "run_turn", forbidden)
    response = client.post(
        f"/api/v1/admin-assistant/sessions/{sid}/turns",
        params={"org_id": str(p.org_id)},
        headers=demo[2]["admin"],
        json={"text": f"这是我的密钥{canary}", "page": "/admin"},
    )
    assert response.status_code == 200 and canary not in response.text
    assert response.json()["messages"][0]["text"] == "请用安全输入框填写"
    session = store.get_session(db, p, sid)
    assert session.state_ciphertext is None
    assert messages.messages_for(db, session) == []
    assert not core.looks_like_secret(
        "查看审批规则，员工邮箱 admin@example.com " + str(UUID(int=1))
    )


def test_secure_columns_migration(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b278001bc001")
    assert not {"secrets", "confirmed_once_at"} & {
        c["name"] for c in inspect(migration_engine).get_columns("admin_change_set")
    }
    migrate(migration_engine, "upgrade", "head")
    assert {"secrets", "confirmed_once_at"} <= {
        c["name"] for c in inspect(migration_engine).get_columns("admin_change_set")
    }


def test_applied_snapshot_excludes_secrets(
    db: Session,
    demo: tuple,
    monkeypatch: pytest.MonkeyPatch,
    secret_function: list[str],
) -> None:
    p = principal(db, demo)
    canary = "sk-synthetic-applied-snapshot-canary"
    entry = registry.FUNCTIONS["test_secret"]
    monkeypatch.setitem(
        registry.FUNCTIONS,
        entry.name,
        replace(entry, snapshot=lambda *_: RootModel({"api_key": canary})),
    )
    row = draft(db, demo, entry.name, {})
    c.save_secret(db, p, row.id, "api_key", canary)
    applied = c.apply(db, p, row.id, row.revision)
    assert applied.status == "applied" and secret_function == [canary]
    assert applied.items[0].after == {}
    assert canary not in applied.model_dump_json()
    assert canary not in json.dumps(c.get(db, p, row.id).items)
