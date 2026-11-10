import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.secrets import decrypt_secret
from app.domains.access_token import service as mcp
from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import core, store
from app.domains.admin_assistant.schemas import AdminTurnInput
from app.domains.audit.models import AuditEvent
from app.domains.bot import store as messages
from app.domains.im import settings as im
from app.domains.im.adapters.base import ImError
from app.domains.im.models import ImSetting
from app.domains.im.schemas import ImSettingUpdate
from app.domains.model.pi_client import TurnResult
from tests.admin_assistant.test_change_set import proposal
from tests.admin_assistant.test_registry import principal
from tests.domains.test_bot_core import enable

CANARY = "sk-synthetic-feishu-secret-283"


@pytest.mark.parametrize("function", ["update_im_setting", "update_mcp_setting"])
def test_confirm_undo_and_web_parity(
    db: Session, demo: tuple, client: TestClient, function: str
) -> None:
    p = principal(db, demo)
    is_im = function == "update_im_setting"
    path = "/im-settings/feishu" if is_im else "/mcp-setting"
    if is_im:
        im.save_setting(
            db,
            p,
            "feishu",
            ImSettingUpdate(
                credentials={
                    "app_id": "synthetic-id",
                    "app_secret": "old-synthetic-secret",
                }
            ),
        )
    row = proposal(db, demo, items=[{"function": function, "args": {"enabled": True}}])
    if is_im:
        c.save_secret(db, p, row.id, "app_secret", CANARY)
    assert c.apply(db, p, row.id, row.revision).status == "draft"

    def read() -> object:
        return (
            im.public_setting(db, p.org_id, "feishu")
            if is_im
            else mcp.get_setting(db, p)
        )

    assert not read().enabled
    assert c.apply(db, p, row.id, row.revision).status == "applied"
    assert read().enabled
    if is_im:
        assert "凭据不回滚" in c.public(row).items[0].impact.text
    assert c.undo(db, p, row.id).status == "applied"
    assert not read().enabled
    if is_im:
        assert (
            json.loads(
                decrypt_secret(db.get(ImSetting, (p.org_id, "feishu")).credentials)
            )["app_secret"]
            == CANARY
        )
    body = (
        {"enabled": True, "data_transfer_acknowledged": True}
        if is_im
        else {"enabled": True}
    )
    assert (
        client.put(demo[0] + path, headers=demo[2]["admin"], json=body).status_code
        == 200
    )
    db.expire_all()
    ordinary = proposal(
        db, demo, items=[{"function": function, "args": {"enabled": True}}]
    )
    assert ordinary.items[0]["risk"] == "normal"
    c.discard(db, p, ordinary.id)
    closed = proposal(
        db, demo, items=[{"function": function, "args": {"enabled": False}}]
    )
    assert closed.items[0]["risk"] == "normal"
    assert c.apply(db, p, closed.id, closed.revision).status == "applied"
    assert not read().enabled
    details = db.exec(
        select(AuditEvent)
        .where(
            AuditEvent.org_id == p.org_id,
            AuditEvent.action
            == ("im_settings.updated" if is_im else "mcp_setting.updated"),
        )
        .order_by(col(AuditEvent.created_at), col(AuditEvent.id))
    ).all()
    assert details[-1].details["via"] == "admin_assistant"
    assert CANARY not in str(details) and CANARY not in str(row.items)


def test_missing_im_credentials_roll_back_whole_set(db: Session, demo: tuple) -> None:
    p = principal(db, demo)
    row = proposal(
        db,
        demo,
        items=[
            {"function": "update_mcp_setting", "args": {"enabled": True}},
            {"function": "update_im_setting", "args": {"enabled": True}},
        ],
    )
    c.apply(db, p, row.id, row.revision)
    with pytest.raises(c.ChangeSetError) as error:
        c.apply(db, p, row.id, row.revision)
    assert "请填写完整的应用凭据" in error.value.detail
    assert "[" not in error.value.detail and "{" not in error.value.detail
    assert not mcp.get_setting(db, p).enabled
    assert not im.public_setting(db, p.org_id, "feishu").enabled
    assert c.get(db, p, row.id).status == "draft"


@pytest.mark.parametrize("platform", ["wecom", "telegram"])
def test_only_feishu_is_available(db: Session, demo: tuple, platform: str) -> None:
    with pytest.raises(c.ChangeSetError, match="请到网页上修改"):
        proposal(
            db,
            demo,
            items=[{"function": "update_im_setting", "args": {"platform": platform}}],
        )


@pytest.mark.parametrize("outcome", ["success", "error", "exception"])
def test_draft_test_and_canary_boundaries(
    db: Session,
    demo: tuple,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    outcome: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    p = principal(db, demo)
    im.save_setting(
        db,
        p,
        "feishu",
        ImSettingUpdate(credentials={"app_id": "old-id", "app_secret": "old-secret"}),
    )
    before = db.get(ImSetting, (p.org_id, "feishu")).model_dump(mode="json")
    row = proposal(
        db, demo, items=[{"function": "update_im_setting", "args": {"enabled": True}}]
    )
    url = f"{demo[0]}/admin-assistant/change-sets/{row.id}"
    for field, value in (("app_id", "new-synthetic-id"), ("app_secret", CANARY)):
        assert (
            client.put(
                url + f"/secrets/{field}",
                headers=demo[2]["admin"],
                json={"value": value},
            ).status_code
            == 200
        )

    async def probe(adapter: im.Feishu) -> str:
        assert adapter.credentials == {
            "app_id": "new-synthetic-id",
            "app_secret": CANARY,
        }
        if outcome == "error":
            raise ImError(CANARY)
        if outcome == "exception":
            raise RuntimeError(CANARY)
        return CANARY

    monkeypatch.setattr(im.Feishu, "test_connection", probe)
    result = client.post(url + "/test", headers=demo[2]["admin"], json={"item": 0})
    assert result.status_code == 200 and result.json()["ok"] == (outcome == "success")
    assert CANARY not in result.text
    db.expire_all()
    assert db.get(ImSetting, (p.org_id, "feishu")).model_dump(mode="json") == before
    for role in ("foreign", "employee", "manager"):
        assert (
            client.post(
                url + "/test", headers=demo[2][role], json={"item": 0}
            ).status_code
            == 404
        )
        assert (
            client.post(
                url + "/apply", headers=demo[2][role], json={"revision": 1}
            ).status_code
            == 404
        )
    assert CANARY not in client.get(url, headers=demo[2]["admin"]).text
    for expected in ("draft", "applied"):
        applied = client.post(
            url + "/apply", headers=demo[2]["admin"], json={"revision": row.revision}
        )
        assert applied.status_code == 200 and applied.json()["status"] == expected
        assert CANARY not in applied.text
    enable(db, p.org_id)

    def reply(_config: object, **kwargs: object) -> TurnResult:
        assert CANARY not in str(kwargs) and "new-synthetic-id" not in str(kwargs)
        return TurnResult(
            text="请核对确认卡", stop_reason="stop", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(core.pi_client, "run_turn", reply)
    core.turn(
        db, p, row.session_id, AdminTurnInput(text="查看飞书连接结果", page="/admin")
    )
    assert CANARY not in str(
        messages.messages_for(db, store.get_session(db, p, row.session_id))
    )
    assert CANARY not in str(
        db.exec(select(AuditEvent).where(AuditEvent.org_id == p.org_id)).all()
    )
    assert CANARY not in caplog.text
