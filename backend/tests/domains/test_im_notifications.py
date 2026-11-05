import asyncio
import json
import logging
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.db import engine
from app.domains.base import utc_now
from app.domains.change_request.models import (
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.im import binding, gateway, notifications
from app.domains.im import settings as im_settings
from app.domains.im.adapters.telegram import Telegram
from app.domains.im.models import ImBinding, ImNotification
from app.domains.im.schemas import ImSettingUpdate
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.reminders import generate_questions
from app.domains.inbox.service import add_item, notify
from app.domains.org.models import OrgMembership
from app.models import User
from tests.domains.test_im_gateway import TOKEN, enable, principal
from tests.utils.migrations import migrate


@pytest.fixture
def bound(demo: tuple, db: Session) -> tuple:
    p = principal(db, demo[1]["E02"].org_id, demo[1]["E02"].id)
    setting = enable(db, p)
    code = binding.create_code(db, p).code
    assert binding.bind(db, p.org_id, "telegram", "100", "100", "12345", code)
    row = db.exec(select(ImBinding).where(ImBinding.org_id == p.org_id)).one()
    return p, setting, row


def queue(db: Session, bound: tuple) -> ImNotification:
    p, _, _ = bound
    add_item(
        db,
        InboxItem(
            org_id=p.org_id,
            recipient_employee_id=p.employee_id,
            kind=InboxKind.QUESTION,
            message="合成的证据正文与画像陈述",
        ),
    )
    db.commit()
    return db.exec(
        select(ImNotification).where(ImNotification.org_id == p.org_id)
    ).one()


def deliver(bound: tuple) -> None:
    p, setting, _ = bound

    async def run() -> None:
        adapter = Telegram(p.org_id, {"bot_token": TOKEN})
        try:
            await notifications.send_pending(
                engine, {(p.org_id, "telegram"): (setting.credentials, adapter)}
            )
        finally:
            await adapter.close()

    asyncio.run(run())


def test_all_kinds_and_periodic_reminders_are_private_and_unread(
    demo: tuple, db: Session, bound: tuple, telegram_server: dict
) -> None:
    p, _, _ = bound
    request = ProfileChangeRequest(
        org_id=p.org_id,
        subject_employee_id=p.employee_id,
        origin=RequestOrigin.MANUAL,
        status=RequestStatus.DRAFT,
        author={},
    )
    db.add(request)
    db.flush()
    for kind in InboxKind:
        notify(db, request, kind, "合成的证据正文与画像陈述")
    generate_questions(
        db, demo[1]["E02"], utc_now(), 24, utc_now() - timedelta(days=90)
    )
    db.commit()
    before = {
        i.id: (i.read_at, i.done_at)
        for i in db.exec(select(InboxItem).where(InboxItem.org_id == p.org_id)).all()
    }
    deliver(bound)
    deliver(bound)
    rows = db.exec(
        select(ImNotification).where(ImNotification.org_id == p.org_id)
    ).all()
    assert len(rows) > len(InboxKind)
    assert all(r.status == "sent" and r.attempts == 1 and r.sent_at for r in rows)
    sends = [b for m, b in telegram_server["requests"] if m == "sendMessage"]
    assert len(sends) == len(rows)
    assert {b["text"] for b in sends} == {
        message + "，在网页或私聊我查看。"
        for message in notifications.REMINDERS.values()
    }
    assert all(b["chat_id"] == "100" for b in sends)
    assert "合成的证据正文与画像陈述" not in json.dumps(sends, ensure_ascii=False)
    db.expire_all()
    assert before == {
        i.id: (i.read_at, i.done_at)
        for i in db.exec(select(InboxItem).where(InboxItem.org_id == p.org_id)).all()
    }


def test_user_recipients_opt_out_and_transaction_rollback(
    db: Session, bound: tuple
) -> None:
    p, _, _ = bound
    assert p.user_id
    admin_id = db.exec(
        select(OrgMembership.user_id).where(
            OrgMembership.org_id == p.org_id, col(OrgMembership.employee_id).is_(None)
        )
    ).first()
    assert admin_id
    for user_id in (p.user_id, admin_id):
        add_item(
            db,
            InboxItem(
                org_id=p.org_id,
                recipient_user_id=user_id,
                kind=InboxKind.REVIEW_REQUESTED,
                message="合成审批通知",
            ),
        )
    db.flush()
    assert (
        len(
            db.exec(
                select(ImNotification).where(ImNotification.org_id == p.org_id)
            ).all()
        )
        == 1
    )
    db.rollback()
    assert not db.exec(
        select(ImNotification).where(ImNotification.org_id == p.org_id)
    ).all()
    binding.set_notify(db, p, "telegram", False)
    add_item(
        db,
        InboxItem(
            org_id=p.org_id,
            recipient_employee_id=p.employee_id,
            kind=InboxKind.QUESTION,
            message="合成问题",
        ),
    )
    db.commit()
    assert not db.exec(
        select(ImNotification).where(ImNotification.org_id == p.org_id)
    ).all()


@pytest.mark.parametrize(
    "change",
    [
        "opt_out",
        "unbind",
        "disabled",
        "credentials",
        "bot",
        "rebind",
        "password",
        "inactive",
    ],
)
def test_pending_is_invalidated(
    db: Session, bound: tuple, telegram_server: dict, change: str
) -> None:
    p, setting, row = bound
    pending = queue(db, bound)
    user = db.get(User, p.user_id)
    old = user.password_changed_at, user.is_active
    try:
        if change == "opt_out":
            binding.set_notify(db, p, "telegram", False)
            binding.set_notify(db, p, "telegram", True)
        elif change == "unbind":
            binding.unbind(db, p, "telegram")
        elif change == "disabled":
            im_settings.save_setting(db, p, "telegram", ImSettingUpdate(enabled=False))
            im_settings.save_setting(
                db,
                p,
                "telegram",
                ImSettingUpdate(enabled=True, data_transfer_acknowledged=True),
            )
        elif change == "bot":
            setting.bot_identity = "99999"
        elif change == "credentials":
            im_settings.save_setting(
                db,
                p,
                "telegram",
                ImSettingUpdate(
                    enabled=True,
                    data_transfer_acknowledged=True,
                    credentials={"bot_token": "99999:synthetic-token"},
                ),
            )
        elif change == "rebind":
            row.created_at = utc_now() + timedelta(seconds=1)
        elif change == "password":
            user.password_changed_at = utc_now()
        else:
            user.is_active = False
        db.add_all([setting, user])
        if change == "rebind":
            db.add(row)
        db.commit()
        deliver(bound)
        db.refresh(pending)
        assert pending.status == "failed" and pending.attempts == 0
        assert not telegram_server["requests"]
    finally:
        user.password_changed_at, user.is_active = old
        db.add(user)
        db.commit()


@pytest.mark.parametrize("recover", [False, True])
def test_retry_limit_and_redacted_errors(
    db: Session,
    bound: tuple,
    telegram_server: dict,
    caplog: pytest.LogCaptureFixture,
    recover: bool,
) -> None:
    row = queue(db, bound)
    caplog.set_level(logging.DEBUG)
    telegram_server["responses"]["sendMessage"] = (
        500,
        b"secret-upstream-evidence-and-token",
    )
    deliver(bound)
    db.refresh(row)
    assert (
        row.status == "pending"
        and row.attempts == 1
        and row.last_error_code == "unavailable"
    )
    if recover:
        telegram_server["responses"].clear()
    for _ in range(3):
        deliver(bound)
    db.refresh(row)
    assert (row.status, row.attempts) == (("sent", 2) if recover else ("failed", 3))
    sends = [b for m, b in telegram_server["requests"] if m == "sendMessage"]
    assert len(sends) == row.attempts
    assert "secret-upstream" not in caplog.text and TOKEN not in caplog.text
    assert "secret-upstream" not in repr(row)


def test_disconnected_adapter_waits_and_gateway_delivers(
    db: Session,
    bound: tuple,
    telegram_server: dict,  # noqa: ARG001
) -> None:
    row = queue(db, bound)
    asyncio.run(notifications.send_pending(engine, {}))
    db.refresh(row)
    assert row.status == "pending" and row.attempts == 0

    async def run() -> None:
        p, _, _ = bound
        g = gateway.Gateway(engine)
        await g.refresh()
        try:
            async with asyncio.timeout(5):
                while (p.org_id, "telegram") not in g.adapters:
                    await asyncio.sleep(0.01)
            await notifications.send_pending(engine, g.adapters)
        finally:
            for _, task in g.tasks.values():
                task.cancel()
            await asyncio.gather(
                *(task for _, task in g.tasks.values()), return_exceptions=True
            )
        assert not g.adapters

    asyncio.run(run())
    db.refresh(row)
    assert row.status == "sent"


def test_toggle_only_changes_own_binding(
    demo: tuple, bound: tuple, client: TestClient
) -> None:
    base, _, auth = demo
    assert bound[0].org_id
    url = base + "/me/im-bindings/telegram"
    for role in ("admin", "manager", "foreign"):
        assert (
            client.patch(url, json={"notify": False}, headers=auth[role]).status_code
            == 404
        )
    assert (
        client.patch(
            url, json={"notify": "false"}, headers=auth["employee"]
        ).status_code
        == 422
    )
    assert (
        client.patch(
            url,
            json={"notify": False, "employee_id": str(uuid4())},
            headers=auth["employee"],
        ).status_code
        == 422
    )
    for enabled in (False, True):
        assert (
            client.patch(
                url, json={"notify": enabled}, headers=auth["employee"]
            ).status_code
            == 204
        )
        assert (
            client.get(base + "/me/im-bindings", headers=auth["employee"]).json()[0][
                "notify"
            ]
            is enabled
        )


def test_notification_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b126001bc001")
    with migration_engine.connect() as conn:
        assert "im_notification" not in inspect(conn).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as conn:
        assert {
            tuple(fk["constrained_columns"])
            for fk in inspect(conn).get_foreign_keys("im_notification")
        } >= {("org_id", "inbox_item_id"), ("org_id", "employee_id")}
        assert (
            conn.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 1
        )


def test_notification_rejects_cross_tenant_reference(db: Session, bound: tuple) -> None:
    p, _, _ = bound
    pending = queue(db, bound)
    foreign_org_id = db.exec(
        select(OrgMembership.org_id).where(OrgMembership.org_id != p.org_id)
    ).first()
    db.add(
        ImNotification(
            org_id=foreign_org_id,
            employee_id=p.employee_id,
            inbox_item_id=pending.inbox_item_id,
            platform="telegram",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
