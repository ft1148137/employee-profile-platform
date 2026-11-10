import json
from datetime import timedelta

import pytest
from sqlalchemy import Engine, inspect
from sqlmodel import Session

from app.core.secrets import decrypt_secret
from app.domains.admin_assistant import core as admin_core
from app.domains.admin_assistant import store as admin_store
from app.domains.admin_assistant.schemas import AdminTurnInput
from app.domains.base import utc_now
from app.domains.bot import core, store
from app.domains.bot.models import BotSetting
from app.domains.model.client import ModelUnavailable
from app.domains.model.pi_client import PiSessionInvalid, TurnResult
from tests.api.routes.test_employee_requests import principal_for
from tests.domains.test_admin_assistant import admin
from tests.domains.test_bot_core import enable
from tests.utils.migrations import migrate
from tests.utils.pi import PiHarness


@pytest.mark.parametrize("is_admin", [False, True])
def test_multiturn_state_and_failure(
    demo: tuple,
    db: Session,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
    is_admin: bool,
) -> None:
    org_id = demo[1]["E02"].org_id
    p = admin(db, org_id) if is_admin else principal_for(db, org_id)
    enable(db, org_id)
    row = (admin_store if is_admin else store).create_session(db, p)
    monkeypatch.setattr(
        admin_core if is_admin else core, "model_config", lambda _: pi_service.config
    )
    name = "get_today" if is_admin else "get_my_profile"
    pi_service.replies = [{"calls": [{"name": name, "arguments": {}}]}]

    def turn() -> object:
        if is_admin:
            return admin_core.turn(
                db, p, row.id, AdminTurnInput(text="合成问题", page="/admin")
            )
        return core.turn(db, p, row.id, "合成问题")

    turn()
    db.refresh(row)
    first = row.state_ciphertext
    assert first and "合成问题" not in first
    state = json.loads(decrypt_secret(first))
    assert any(m["role"] == "toolResult" for m in state["messages"])
    pi_service.replies = ["继续回答"]
    turn()
    sent = pi_service.requests[-1]["body"]["messages"]
    assert any(m["role"] == "tool" for m in sent)
    assert any(m["role"] == "assistant" and m.get("tool_calls") for m in sent)
    db.refresh(row)
    previous = row.state_ciphertext
    for failure in (ModelUnavailable("超时"), PiSessionInvalid(), "length"):

        def fail(
            *_args: object, failure: object = failure, **_kwargs: object
        ) -> TurnResult:
            if isinstance(failure, Exception):
                raise failure
            return TurnResult(
                text="未完成",
                stop_reason="length",
                model_calls=1,
                tool_calls=0,
                session_state={"version": 1, "messages": []},
            )

        with monkeypatch.context() as patch:
            patch.setattr(core.pi_client, "run_turn", fail)
            response = turn()
        if isinstance(failure, PiSessionInvalid):
            assert "会话已失效，请新开对话" in str(response)
        db.refresh(row)
        assert row.state_ciphertext == previous


@pytest.mark.parametrize("is_admin", [False, True])
@pytest.mark.parametrize("days", [0, 7])
def test_state_retention(demo: tuple, db: Session, is_admin: bool, days: int) -> None:
    org_id = demo[1]["E02"].org_id
    p = admin(db, org_id) if is_admin else principal_for(db, org_id)
    row = (admin_store if is_admin else store).create_session(db, p)
    db.add(BotSetting(org_id=org_id, retention_days=days))
    message = store.append_message(db, row, "user", "合成历史")
    store.save_state(db, row, {"version": 1, "messages": ["opaque"]})
    db.commit()
    assert store.load_state(db, row) == {"version": 1, "messages": ["opaque"]}
    now = utc_now()
    message.created_at = now - timedelta(days=8)
    row.last_activity_at = now - timedelta(hours=3)
    db.add_all([row, message])
    db.commit()
    assert store.load_state(db, row) is None
    store.cleanup_messages(db, now)
    db.refresh(row)
    assert row.state_ciphertext is None and store.messages_for(db, row) == []
    store.append_message(db, row, "user", "新的输入")
    db.commit()
    assert store.load_state(db, row) is None


def test_state_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b274001bc001")
    for table in ("bot_session", "admin_assistant_session"):
        assert "state_ciphertext" not in {
            c["name"] for c in inspect(migration_engine).get_columns(table)
        }
    migrate(migration_engine, "upgrade", "head")
    for table in ("bot_session", "admin_assistant_session"):
        column = next(
            c
            for c in inspect(migration_engine).get_columns(table)
            if c["name"] == "state_ciphertext"
        )
        assert column["nullable"]
