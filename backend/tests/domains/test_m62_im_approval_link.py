import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.domains.capacity.workload import (
    EXTENSION_REVIEW_NOTICE,
    WORKLOAD_REVIEW_NOTICE,
)
from app.domains.im import notifications
from app.domains.im.models import ImBinding, ImNotification, ImSetting
from app.domains.im.schemas import Platform
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.service import add_item
from tests.domains.test_im_gateway import principal


@pytest.mark.parametrize("platform", ["feishu", "wecom", "telegram"])
@pytest.mark.parametrize(
    "frontend_host", ["https://example.com", "https://example.com/"]
)
def test_capacity_approval_links_are_fixed_and_private(
    demo: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    platform: Platform,
    frontend_host: str,
) -> None:
    subject = demo[1]["E02"]
    p = principal(db, subject.org_id, subject.id)
    monkeypatch.setattr(settings, "FRONTEND_HOST", frontend_host)
    setting = ImSetting(
        org_id=p.org_id,
        platform=platform,
        enabled=True,
        credentials="synthetic",
        bot_identity="synthetic",
        updated_by_user_id=p.user_id,
    )
    db.add(setting)
    db.add(
        ImBinding(
            org_id=p.org_id,
            employee_id=subject.id,
            platform=platform,
            external_user_id="synthetic",
            external_chat_id="synthetic",
            bot_identity="synthetic",
        )
    )
    db.commit()
    private_message = f"{subject.display_name} 2026-11-16 合成申请内容"
    for kind, message in [
        (InboxKind.CAPACITY, WORKLOAD_REVIEW_NOTICE),
        (InboxKind.CAPACITY, EXTENSION_REVIEW_NOTICE),
        (InboxKind.CAPACITY, f"{WORKLOAD_REVIEW_NOTICE}（{private_message}）"),
        (InboxKind.CAPACITY, f"{EXTENSION_REVIEW_NOTICE}（{private_message}）"),
        (InboxKind.CAPACITY, private_message),
        (InboxKind.REVIEW_REQUESTED, WORKLOAD_REVIEW_NOTICE),
    ]:
        add_item(
            db,
            InboxItem(
                org_id=p.org_id,
                recipient_employee_id=subject.id,
                kind=kind,
                message=message,
            ),
        )
    db.commit()
    adapter = AsyncMock()
    asyncio.run(
        notifications.send_pending(
            engine, {(p.org_id, platform): (setting.credentials, adapter)}
        )
    )
    texts = [call.args[1] for call in adapter.send_text.await_args_list]
    approval_text = (
        notifications.REMINDERS[InboxKind.CAPACITY]
        + "，在网页处理：https://example.com/reviews?type=workload"
    )
    assert texts == [
        approval_text,
        approval_text,
        approval_text,
        approval_text,
        notifications.REMINDERS[InboxKind.CAPACITY] + "，在网页或私聊我查看。",
        notifications.REMINDERS[InboxKind.REVIEW_REQUESTED] + "，在网页或私聊我查看。",
    ]
    for private in (subject.display_name, "2026-11-16", "合成申请内容"):
        assert all(private not in text for text in texts)
    rows = db.exec(
        select(ImNotification).where(ImNotification.org_id == p.org_id)
    ).all()
    assert len(rows) == 6
    assert all(row.status == "sent" and row.attempts == 1 for row in rows)
