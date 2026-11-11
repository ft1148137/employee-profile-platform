import asyncio
from datetime import datetime
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import connections, registry, store, tools
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.prompts import system_prompt
from app.domains.admin_assistant.schemas import AdminSessionMessage
from app.domains.base import utc_now
from app.domains.bot.prompts import system_prompt as employee_prompt
from app.domains.drafting.update_settings import (
    UpdateSettingsInput,
    save_update_settings,
)
from app.domains.im.adapters.base import ImError
from tests.admin_assistant.test_change_set import proposal
from tests.admin_assistant.test_registry import principal


def test_unchanged_settings_return_tool_error_without_card(
    db: Session, demo: tuple
) -> None:
    p = principal(db, demo)
    sid = store.create_session(db, p).id
    save_update_settings(db, p, UpdateSettingsInput(reminder_schedule="quarterly"))
    cards = []
    result = tools.call(
        db,
        p,
        cards,
        "propose_changes",
        {
            "items": [
                {
                    "function": "update_update_settings",
                    "args": {"reminder_schedule": "quarterly"},
                }
            ]
        },
        session_id=sid,
    )
    assert result == {"error": "设置已经是这个值，不需要改动"}
    assert cards == []
    assert not db.exec(
        select(AdminChangeSet).where(AdminChangeSet.session_id == sid)
    ).all()
    row = proposal(
        db,
        demo,
        items=[
            {"function": "update_update_settings", "args": {"stale_after_months": 36}}
        ],
    )
    assert row.items[0]["after"]["reminder_schedule"] == "quarterly"
    assert row.items[0]["after"]["stale_after_months"] == 36


def test_undo_after_manual_restore_still_creates_reverse(
    db: Session, demo: tuple
) -> None:
    p = principal(db, demo)
    row = proposal(db, demo)
    before = row.items[0]["before"]
    c.apply(db, p, row.id, row.revision)
    save_update_settings(db, p, UpdateSettingsInput.model_validate(before))
    reverse = c.undo(db, p, row.id)
    assert reverse.status == "draft" and reverse.undo_of_id == row.id
    assert reverse.items[0].before == reverse.items[0].after


def test_table_time_and_plain_text_prompts(db: Session, shared_demo: tuple) -> None:
    p = principal(db, shared_demo)
    started = utc_now()
    result = asyncio.run(registry.admin_read(db, p, "list_accounts", {}))
    assert (
        started <= datetime.fromisoformat(result["cards"][0]["queried_at"]) <= utc_now()
    )
    prompt = system_prompt(p, "/admin")
    assert "这份草稿里的新设置" in prompt and "由管理员点击，你没有这个工具" in prompt
    assert "test_jira_connection、test_im_connection、test_model" in prompt
    assert "已经保存的配置" in prompt and "不要混为一谈" in prompt
    assert "纯文本" in prompt and "纯文本" in employee_prompt(
        db, principal(db, shared_demo, "employee"), "web"
    )


@pytest.mark.parametrize("missing", ["app_id", "app_secret"])
def test_feishu_names_missing_field(db: Session, demo: tuple, missing: str) -> None:
    values = {"app_id": "synthetic-id", "app_secret": "synthetic-key"}
    del values[missing]
    result = asyncio.run(
        connections.test_im(db, principal(db, demo), connections.FeishuArgs(**values))
    )
    assert not result.ok
    assert ("App ID" if missing == "app_id" else "App Secret") in result.message
    assert "凭证与基础信息" in result.message
    assert "synthetic" not in result.message


def test_feishu_preserves_classification_and_first_step(
    db: Session, demo: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(_adapter: object) -> str:
        raise ImError("credentials")

    monkeypatch.setattr(connections.im.Feishu, "test_connection", fail)
    result = asyncio.run(
        connections.test_im(
            db,
            principal(db, demo),
            connections.FeishuArgs(app_id="synthetic-id", app_secret="synthetic-key"),
        )
    )
    assert not result.ok
    assert "应用凭据无效或没有权限" in result.message
    assert "第一步" in result.message and "飞书开放平台" in result.message
    assert "synthetic" not in result.message


def test_old_table_history_does_not_gain_a_query_time() -> None:
    message = AdminSessionMessage.model_validate(
        {
            "id": str(uuid4()),
            "role": "assistant",
            "created_at": utc_now(),
            "payload": {
                "messages": [
                    {
                        "type": "table",
                        "title": "历史表格",
                        "columns": [],
                        "rows": [],
                        "total": 0,
                        "page": "/admin",
                    }
                ]
            },
        }
    )
    assert message.payload.messages[0].queried_at is None
