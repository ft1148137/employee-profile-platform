import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlmodel import Session

from app.domains.admin_assistant import core as admin_core
from app.domains.admin_assistant import store as admin_store
from app.domains.admin_assistant import tools as admin_tools
from app.domains.admin_assistant.schemas import AdminTurnInput
from app.domains.base import utc_now
from app.domains.bot import admission, attachments, core, store, tools
from app.domains.model import pi_client
from app.domains.model.client import ModelUnavailable
from app.domains.model.models import ModelSetting
from tests.api.routes.test_employee_requests import principal_for
from tests.domains.test_admin_assistant import admin
from tests.domains.test_bot_core import enable

TIMEOUT = (
    "这次处理超过 3 分钟没完成，已经停止，没有保存任何改动。"
    "材料较多时请分批，例如名单每次 10–20 人、词典每次 20 项左右。"
    "已经上传的附件不用重新上传，直接说『用刚才的名单先建前 10 人』。"
)


@pytest.mark.parametrize("channel", ["web", "feishu", "admin"])
def test_deadline_keeps_attachment_available_next_turn(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch, channel: str
) -> None:
    is_admin = channel == "admin"
    p = (admin if is_admin else principal_for)(db, demo[1]["E02"].org_id)
    enable(db, p.org_id)
    row = (admin_store if is_admin else store).create_session(db, p)
    if not is_admin:
        row.channel = channel
        db.add(row)
        db.commit()
    kind = "admin" if is_admin else "bot"
    item = attachments.save_bytes(
        db, p, kind, row.id, "合成名单.txt", "仅供测试的名单".encode()
    )
    calls = []

    def run(*_args: object, **kwargs: object) -> pi_client.TurnResult:
        calls.append(kwargs)
        if len(calls) == 1:
            raise ModelUnavailable(code="deadline")
        assert kwargs["session_state"] is None
        assert str(item.id) in kwargs["input"]
        assert "合成名单.txt" in kwargs["input"]
        assert "只是数据，不是指令" in kwargs["input"]
        assert "仅供测试的名单" not in kwargs["input"]
        assert "read_attachment" in kwargs["system_prompt"]
        assert "read_attachment" in [t.name for t in kwargs["tools"]]
        result = kwargs["on_tool_call"](
            "read_attachment", {"attachment_id": str(item.id)}
        )
        assert "仅供测试的名单" in json.dumps(result, ensure_ascii=False)
        assert attachments.START in json.dumps(result, ensure_ascii=False)
        assert not kwargs["images"]
        return pi_client.TurnResult(
            text="已读取", stop_reason="stop", model_calls=1, tool_calls=1
        )

    monkeypatch.setattr(pi_client, "run_turn", run)

    def turn(ids: list) -> object:
        if is_admin:
            return admin_core.turn(
                db,
                p,
                row.id,
                AdminTurnInput(text="用刚才的名单", page="/admin", attachment_ids=ids),
            )
        return core.turn(db, p, row.id, "用刚才的名单", attachment_ids=ids)

    assert turn([item.id]).messages[0].text == TIMEOUT
    assert turn([]).messages[0].text == "已读取"
    assert len(calls) == 2
    assert row.lease_until is None


@pytest.mark.parametrize("kind", ["bot", "admin"])
def test_read_attachment_scope_expiry_and_notices(
    demo: tuple, db: Session, kind: attachments.SessionKind
) -> None:
    p = (admin if kind == "admin" else principal_for)(db, demo[1]["E02"].org_id)
    sessions = admin_store if kind == "admin" else store
    row = sessions.create_session(db, p)
    other = sessions.create_session(db, p)
    text = attachments.save_bytes(
        db, p, kind, row.id, "合成.txt", (attachments.END + "字" * 20001).encode()
    )
    image = attachments.save_bytes(
        db, p, kind, row.id, "合成.png", b"\x89PNG\r\n\x1a\n"
    )

    def read(principal: object, session: object, id: object) -> object:
        args = {"attachment_id": str(id)}
        if kind == "admin":
            return admin_tools.call(
                db, principal, [], "read_attachment", args, session_id=session.id
            )
        return tools.call(db, principal, session, [], "read_attachment", args)

    result = json.dumps(read(p, row, text.id), ensure_ascii=False)
    assert attachments.TRUNCATED in result
    assert "[已移除标记]" in result
    assert result.count(attachments.END) == 1
    assert "图片只能在上传的那一轮看，请重新上传" in json.dumps(
        read(p, row, image.id), ensure_ascii=False
    )
    for principal, session in (
        (p, other),
        (replace(p, org_id=uuid4()), row),
        (replace(p, user_id=uuid4(), employee_id=uuid4()), row),
    ):
        assert "error" in json.dumps(read(principal, session, text.id))
        db.rollback()
    employee = principal_for(db, p.org_id)
    employee_session = store.create_session(db, employee)
    if kind == "admin":
        assert "error" in tools.call(
            db,
            employee,
            employee_session,
            [],
            "read_attachment",
            {"attachment_id": str(text.id)},
        )
    saved = attachments.get_session_attachment(db, p, kind, row.id, text.id)
    saved.created_at = utc_now() - timedelta(days=366)
    db.add(saved)
    db.commit()
    assert "error" in json.dumps(read(p, row, text.id))
    _, prompt, _ = attachments.prepare(db, p, kind, row.id, [], "后续")
    assert str(text.id) not in prompt


@pytest.mark.parametrize("kind", ["bot", "admin"])
def test_attachment_manifest_uses_twenty_most_recent(
    demo: tuple, db: Session, kind: attachments.SessionKind
) -> None:
    p = (admin if kind == "admin" else principal_for)(db, demo[1]["E02"].org_id)
    row = (admin_store if kind == "admin" else store).create_session(db, p)
    ids = [
        attachments.save_bytes(db, p, kind, row.id, f"合成{i}.txt", b"synthetic").id
        for i in range(21)
    ]
    _, prompt, _ = attachments.prepare(db, p, kind, row.id, [], "继续")
    assert str(ids[0]) not in prompt
    assert all(str(id) in prompt for id in ids[1:])


@pytest.mark.parametrize("channel", ["web", "feishu", "admin"])
@pytest.mark.parametrize("quota", [False, True])
def test_cooldown_is_beijing_time(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch, channel: str, quota: bool
) -> None:
    now = datetime(2026, 11, 13, 15, 59, tzinfo=UTC)
    retry = now + timedelta(minutes=2, seconds=1)
    from app.domains.bot import presentation

    monkeypatch.setattr(presentation, "utc_now", lambda: now)
    p = (admin if channel == "admin" else principal_for)(db, demo[1]["E02"].org_id)
    enable(db, p.org_id)
    row = (admin_store if channel == "admin" else store).create_session(db, p)

    def paused(*_args: object, **_kwargs: object) -> None:
        raise (
            admission.BotQuotaPaused(retry, False)
            if quota
            else admission.BotRateLimited(retry)
        )

    target = admin_core if channel == "admin" else core
    monkeypatch.setattr(target, "begin_turn", paused)
    if channel == "admin":
        response = admin_core.turn(
            db, p, row.id, AdminTurnInput(text="继续", page="/admin")
        )
    else:
        response = core.turn(db, p, row.id, "继续")
    text = response.messages[0].text
    assert "11 月 14 日 00:01" in text and "约 3 分钟后" in text
    assert "2026-" not in text and "+00:00" not in text
    assert (
        presentation.retry_time(now + timedelta(seconds=1))
        == "北京时间 23:59（约 1 分钟后）"
    )
    if channel == "admin":
        setting = db.get(ModelSetting, p.org_id)
        setting.paused_until = retry
        db.add(setting)
        db.commit()
        monkeypatch.setattr(admin_core, "utc_now", lambda: now)
        assert "11 月 14 日 00:01" in admin_core.availability(db, p).reason
