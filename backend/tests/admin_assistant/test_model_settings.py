import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.secrets import decrypt_secret, encrypt_secret
from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import core, store
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import AdminTurnInput
from app.domains.audit.models import AuditEvent
from app.domains.bot import settings as bot
from app.domains.bot import store as messages
from app.domains.model import chatgpt, service
from app.domains.model.client import ModelUnavailable
from app.domains.model.pi_client import TurnResult
from app.domains.model.schemas import ModelSettingUpdate
from tests.admin_assistant.test_change_set import proposal
from tests.admin_assistant.test_registry import principal

CONFIG = {
    "enabled": True,
    "kind": "cloud_api",
    "provider": "openai",
    "model": "synthetic",
    "reasoning_effort": "off",
}
SUBSCRIPTION = {"enabled": True, "kind": "chatgpt_subscription"}
CANARY = "sk-synthetic-model-canary-282"


@pytest.fixture(autouse=True)
def models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "validate_capabilities", lambda *_: None)
    monkeypatch.setattr(chatgpt, "local_auth", lambda: False)


def setup(db: Session, demo: tuple, initial: dict | None = None) -> None:
    service.save_setting(
        db,
        principal(db, demo),
        ModelSettingUpdate.model_validate(
            CONFIG | (initial or {}) | {"api_key": "old-synthetic-key"}
        ),
    )


def draft(db: Session, demo: tuple, args: dict) -> AdminChangeSet:
    return proposal(
        db, demo, items=[{"function": "update_model_setting", "args": args}]
    )


@pytest.mark.parametrize(
    "initial,update,high",
    [
        ({}, {"model": "another"}, True),
        ({}, {"provider": "anthropic"}, True),
        ({}, {"enabled": False}, True),
        ({}, {"reasoning_effort": "high"}, False),
        ({}, {}, False),
        ({"enabled": False}, {"enabled": True}, False),
        (
            {
                "kind": "local",
                "provider": "ollama",
                "base_url": "http://localhost:11434",
            },
            {"enabled": False},
            False,
        ),
    ],
)
def test_model_parity_and_confirmation(
    db: Session,
    demo: tuple,
    client: TestClient,
    initial: dict,
    update: dict,
    high: bool,
) -> None:
    setup(db, demo, initial)
    p = principal(db, demo)
    row = draft(db, demo, CONFIG | initial | update)
    c.save_secret(db, p, row.id, "api_key", CANARY)
    result = c.apply(db, p, row.id, row.revision)
    assert (result.status == "draft") == high
    if high:
        assert (
            service.public_setting(service.read_setting(db, p.org_id)).model
            == CONFIG["model"]
        )
        result = c.apply(db, p, row.id, row.revision)
    assert result.status == "applied"
    actual = service.public_setting(service.read_setting(db, p.org_id))
    manual = client.put(
        demo[0] + "/model-setting",
        headers=demo[2]["admin"],
        json=CONFIG | initial | update | {"api_key": CANARY},
    )
    assert manual.status_code == 200
    assert actual.model_dump(mode="json", exclude={"updated_at"}) == {
        k: v for k, v in manual.json().items() if k != "updated_at"
    }
    assert CANARY not in manual.text + result.model_dump_json()


def test_bot_parity_undo_and_atomic_failure(
    db: Session,
    demo: tuple,
    client: TestClient,
) -> None:
    p = principal(db, demo)
    body = {"question_guide": "请结合具体项目提问", "retention_days": 7}
    row = proposal(db, demo, items=[{"function": "update_bot_settings", "args": body}])
    assert c.apply(db, p, row.id, row.revision).status == "applied"
    assert (
        client.get(demo[0] + "/bot-settings", headers=demo[2]["admin"]).json() == body
    )
    assert c.undo(db, p, row.id).status == "applied"
    assert bot.read_settings(db, p.org_id) == bot.BotSettings()
    # A later model business error must roll back the earlier bot settings and audit.
    items = [
        {"function": "update_bot_settings", "args": body},
        {"function": "update_model_setting", "args": SUBSCRIPTION},
    ]
    row = proposal(db, demo, items=items)
    assert c.apply(db, p, row.id, row.revision).status == "draft"
    with pytest.raises(c.ChangeSetError, match="请先在设置页连接 ChatGPT") as error:
        c.apply(db, p, row.id, row.revision)
    assert error.value.status_code == 422
    assert "[" not in error.value.detail and "{" not in error.value.detail
    assert bot.read_settings(db, p.org_id) == bot.BotSettings()
    assert service.read_setting(db, p.org_id) is None
    assert c.get(db, p, row.id).status == "draft"
    assert not db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == p.org_id,
            AuditEvent.details["change_set_id"].astext == str(row.id),
        )
    ).all()
    manual = client.put(demo[0] + "/bot-settings", headers=demo[2]["admin"], json=body)
    assert manual.status_code == 200 and manual.json() == body


@pytest.mark.parametrize("target", ["effort", "provider", "subscription"])
def test_undo_keeps_keys_out_of_snapshots(
    db: Session,
    demo: tuple,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    setup(db, demo)
    p = principal(db, demo)
    if target == "subscription":
        monkeypatch.setattr(chatgpt, "local_auth", lambda: True)
    args = (
        SUBSCRIPTION
        if target == "subscription"
        else CONFIG
        | (
            {"provider": "anthropic"}
            if target == "provider"
            else {"reasoning_effort": "high"}
        )
    )
    row = draft(db, demo, args)
    if target != "subscription":
        c.save_secret(db, p, row.id, "api_key", CANARY)
    if c.apply(db, p, row.id, row.revision).status == "draft":
        assert c.apply(db, p, row.id, row.revision).status == "applied"
    reverse = c.undo(db, p, row.id)
    if target == "effort":
        assert reverse.status == "applied"
    else:
        assert reverse.status == "draft"
        payload = messages.messages_for(db, store.get_session(db, p, row.session_id))[
            -1
        ].payload
        assert payload["messages"][1]["type"] == "secret_input"
        with pytest.raises(c.ChangeSetError, match="重新填写凭据"):
            c.apply(db, p, reverse.id, reverse.revision)
        db.rollback()
        c.save_secret(db, p, reverse.id, "api_key", "restored-synthetic-key")
        assert c.apply(db, p, reverse.id, reverse.revision).status == "draft"
        assert c.apply(db, p, reverse.id, reverse.revision).status == "applied"
    saved = service.read_setting(db, p.org_id)
    assert saved.provider == "openai" and saved.reasoning_effort == "off"
    assert decrypt_secret(saved.api_key_ciphertext) == (
        CANARY if target == "effort" else "restored-synthetic-key"
    )
    assert CANARY not in json.dumps(c.get(db, p, row.id).items)


@pytest.mark.parametrize("outcome", ["ok", "error", "exception"])
def test_probe_uses_draft_without_saving_or_leaking(
    db: Session,
    demo: tuple,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    outcome: str,
) -> None:
    setup(db, demo)
    p = principal(db, demo)
    row = draft(db, demo, CONFIG | {"model": "draft-model", "provider": "anthropic"})
    url = f"{demo[0]}/admin-assistant/change-sets/{row.id}"
    auth = demo[2]["admin"]
    calls = []

    def probe(config: object, **_: object) -> TurnResult:
        calls.append((config.id, config.provider, config.api_key.get_secret_value()))
        if outcome == "error":
            raise ModelUnavailable(CANARY)
        if outcome == "exception":
            raise RuntimeError(CANARY)
        return TurnResult(text=CANARY, stop_reason="stop", model_calls=1, tool_calls=0)

    monkeypatch.setattr(service, "run_turn", probe)
    assert (
        "请先填写新的凭据"
        in client.post(url + "/test", headers=auth, json={"item": 0}).text
    )
    c.save_secret(db, p, row.id, "api_key", CANARY)
    before = service.read_setting(db, p.org_id).model_dump()
    response = client.post(url + "/test", headers=auth, json={"item": 0})
    assert response.status_code == 200 and response.json()["ok"] == (outcome == "ok")
    assert calls == [("draft-model", "anthropic", CANARY)]
    db.expire_all()
    assert service.read_setting(db, p.org_id).model_dump() == before
    for role in ("employee", "manager", "foreign"):
        assert (
            client.post(
                url + "/test", headers=demo[2][role], json={"item": 0}
            ).status_code
            == 404
        )
        assert (
            client.post(
                url + "/apply", headers=demo[2][role], json={"revision": row.revision}
            ).status_code
            == 404
        )
    assert (
        CANARY not in response.text + client.get(url, headers=auth).text + caplog.text
    )

    def reply(_config: object, **kwargs: object) -> TurnResult:
        assert CANARY not in str(kwargs)
        return TurnResult(
            text="请核对草稿", stop_reason="stop", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(core.pi_client, "run_turn", reply)
    core.turn(
        db,
        p,
        row.session_id,
        AdminTurnInput(text="查看测试结果", page="/admin/data-ai"),
    )
    assert CANARY not in str(
        messages.messages_for(db, store.get_session(db, p, row.session_id))
    )
    assert CANARY not in str(
        db.exec(select(AuditEvent).where(AuditEvent.org_id == p.org_id)).all()
    )


def test_subscription_probe_never_refreshes_or_saves(
    db: Session,
    demo: tuple,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup(db, demo)
    p = principal(db, demo)
    row = draft(db, demo, SUBSCRIPTION)
    assert row.items[0]["after"]["model"].startswith("chatgpt/")
    url = f"{demo[0]}/admin-assistant/change-sets/{row.id}/test"
    assert client.post(url, headers=demo[2]["admin"], json={"item": 0}).json() == {
        "ok": False,
        "message": "请先在设置页连接 ChatGPT",
    }
    saved = service.read_setting(db, p.org_id)
    from app.domains.base import utc_now

    saved.chatgpt_auth_ciphertext = encrypt_secret(
        chatgpt.OAuthTokens(
            access=CANARY,
            refresh="unused-synthetic-refresh",
            expires=(utc_now().timestamp() + 3600) * 1000,
            accountId="synthetic",
        ).model_dump_json()
    )
    db.commit()
    db.refresh(saved)
    before = saved.model_dump()
    monkeypatch.setattr(
        chatgpt, "access_token", lambda *_: pytest.fail("must not refresh")
    )
    monkeypatch.setattr(
        service,
        "run_turn",
        lambda config, **_: (
            TurnResult(
                text="成功",
                stop_reason="stop",
                model_calls=1,
                tool_calls=0,
            )
            if config.api_key.get_secret_value() == CANARY and config.org_id is None
            else pytest.fail("draft key")
        ),
    )
    assert client.post(url, headers=demo[2]["admin"], json={"item": 0}).json()["ok"]
    db.refresh(saved)
    assert saved.model_dump() == before
