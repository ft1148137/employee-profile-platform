import json
from datetime import datetime, timedelta
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.domains.bot import core, store, tools
from app.domains.bot import schemas as s
from tests.api.routes.test_employee_requests import principal_for
from tests.api.routes.test_m47_stale_initial_draft import initial_draft, publish
from tests.api.routes.test_mcp import call, initialize, token_for


@pytest.mark.parametrize(("timezone", "hours"), [(None, 8), ("Asia/Tokyo", 9)])
@pytest.mark.parametrize("entry", ["web", "mcp", "web_bot", "feishu_bot"])
def test_stale_draft_time(
    demo: tuple,
    client: TestClient,
    db: Session,
    timezone: str | None,
    hours: int,
    entry: Literal["web", "mcp", "web_bot", "feishu_bot"],
) -> None:
    base, initial = initial_draft(client, demo)
    if timezone:
        saved = client.put(
            f"{demo[0]}/update-settings/timezone",
            headers=demo[2]["admin"],
            json={"timezone": timezone},
        )
        assert saved.status_code == 200, saved.text
    if entry in ("web_bot", "feishu_bot"):
        p = principal_for(db, demo[1]["E02"].org_id)
        session = store.create_session(db, p, "web" if entry == "web_bot" else "feishu")
        cards: list[s.Message] = []
        tools.call(
            db, p, session, cards, "preview_request", {"request_id": initial["id"]}
        )
        card = next(c for c in cards if isinstance(c, s.PreviewCard))
        core._save(db, session, cards)
        db.commit()
    publish(client, demo)
    preview = client.get(f"{base}/preview", headers=demo[2]["employee"]).json()
    changed_at = next(
        i["current_changed_at"]
        for i in preview["items"]
        if i["after"]["capability"]["key"] == "automated_testing"
    )
    expected_time = (
        datetime.fromisoformat(changed_at) + timedelta(hours=hours)
    ).strftime("%Y-%m-%d %H:%M")
    expected_message = (
        f"经理或管理员在 {expected_time} 改过「自动化测试」，"
        "请先打开预览核对，再确认提交"
    )
    if entry in ("web_bot", "feishu_bot"):
        response = core.act(db, p, session.id, card.actions[0])
        message = next(
            m.text for m in response.messages if isinstance(m, s.TextMessage)
        )
        assert any(isinstance(m, s.PreviewCard) for m in response.messages)
    else:
        args = {"request_id": initial["id"], "revision": initial["revision"]}
        if entry == "mcp":
            headers = initialize(client, token_for(db, demo[1]["E02"])[0])
            result = call(client, headers, "submit_request", args)
            assert result["isError"]
            error = json.loads(result["content"][0]["text"])
            assert error["status"] == 409
            detail = error["detail"]
        else:
            result = client.post(
                f"{base}/submit",
                headers=demo[2]["employee"],
                json={"revision": initial["revision"]},
            )
            assert result.status_code == 409, result.text
            detail = result.json()["detail"]
        assert detail["code"] == "stale_initial_draft"
        assert detail["capabilities"] == [
            {
                "key": "automated_testing",
                "name": "自动化测试",
                "changed_at": datetime.fromisoformat(changed_at).isoformat(),
            }
        ]
        message = detail["message"]
    assert message == expected_message
    assert all(token not in message for token in ("+00:00", "T", "preview_request"))
