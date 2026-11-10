import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, select

from app.core.db import engine
from app.domains.admin.overview import AdminOverviewPublic, build_overview
from app.domains.base import utc_now
from app.domains.im import notifications
from app.domains.im.models import ImBinding, ImNotification, ImSetting
from app.domains.inbox import admin_todos
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.org.models import ExternalIdentity, OrgMembership, Role, RoleGrant
from tests.admin_assistant.test_registry import principal
from tests.utils.migrations import migrate


def bind_admin(db: Session, demo: tuple) -> tuple:
    p = principal(db, demo, "employee")
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == p.org_id,
            OrgMembership.user_id == p.user_id,
        )
    ).one()
    db.add(RoleGrant(org_id=p.org_id, membership_id=membership.id, role=Role.ORG_ADMIN))
    setting = ImSetting(
        org_id=p.org_id,
        platform="feishu",
        enabled=True,
        credentials="synthetic-ciphertext",
        bot_identity="synthetic-bot",
        updated_by_user_id=p.user_id,
    )
    db.add(setting)
    for platform in ("feishu", "telegram", "wecom"):
        db.add(
            ImBinding(
                org_id=p.org_id,
                employee_id=p.employee_id,
                platform=platform,
                external_user_id="synthetic-user",
                external_chat_id="synthetic-chat",
                bot_identity="synthetic-bot",
            )
        )
    db.commit()
    return principal(db, demo, "employee"), setting


def test_daily_counts_are_private_and_feishu_only(db: Session, demo: tuple) -> None:
    p, setting = bind_admin(db, demo)
    expected = sum(t.count for t in build_overview(db, p).todos)
    foreign_org = db.exec(
        select(OrgMembership.org_id).where(OrgMembership.org_id != p.org_id)
    ).first()
    for index in range(7):
        db.add(
            ExternalIdentity(
                org_id=foreign_org, source="jira", external_id=f"foreign-{index}"
            )
        )
    db.commit()
    now = utc_now()
    admin_todos.dispatch_admin_todos(db, now)
    admin_todos.dispatch_admin_todos(db, now)
    items = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == p.org_id, InboxItem.kind == InboxKind.ADMIN_TODOS
        )
    ).all()
    assert len(items) == 1
    item = items[0]
    assert item.recipient_user_id == p.user_id and item.admin_todo_count == expected
    assert item.message == f"你有 {expected} 条管理员待办，请在网页首页处理。"
    rows = db.exec(
        select(ImNotification).where(ImNotification.org_id == p.org_id)
    ).all()
    assert len(rows) == 1 and rows[0].platform == "feishu"
    adapter = SimpleNamespace(send_text=AsyncMock())
    # Delivery builds fixed copy from the count, never from arbitrary inbox text.
    item.message = "synthetic-private-employee-name"
    db.add(item)
    db.commit()
    asyncio.run(
        notifications.send_pending(
            engine, {(p.org_id, "feishu"): (setting.credentials, adapter)}
        )
    )
    adapter.send_text.assert_awaited_once_with(
        "synthetic-chat", f"你有 {expected} 条管理员待办，请在网页首页处理。"
    )
    admin_todos.dispatch_admin_todos(db, now + timedelta(days=1))
    assert (
        len(
            db.exec(
                select(InboxItem).where(
                    InboxItem.org_id == p.org_id,
                    InboxItem.kind == InboxKind.ADMIN_TODOS,
                )
            ).all()
        )
        == 2
    )


@pytest.mark.parametrize(
    "skip", ["no_todos", "disabled", "opt_out", "demoted", "old_binding"]
)
def test_digest_skips_ineligible_recipients(
    db: Session,
    demo: tuple,
    monkeypatch: pytest.MonkeyPatch,
    skip: str,
) -> None:
    p, setting = bind_admin(db, demo)
    bound = db.exec(
        select(ImBinding).where(
            ImBinding.org_id == p.org_id, ImBinding.platform == "feishu"
        )
    ).one()
    if skip == "no_todos":
        monkeypatch.setattr(
            admin_todos,
            "build_overview",
            lambda *_: AdminOverviewPublic(status_cards=[], todos=[]),
        )
    elif skip == "disabled":
        setting.enabled = False
        db.add(setting)
    elif skip == "opt_out":
        bound.notify = False
    elif skip == "old_binding":
        bound.bot_identity = "old-bot"
    else:
        membership = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == p.org_id, OrgMembership.user_id == p.user_id
            )
        ).one()
        grant = db.exec(
            select(RoleGrant).where(
                RoleGrant.org_id == p.org_id,
                RoleGrant.membership_id == membership.id,
                RoleGrant.role == Role.ORG_ADMIN,
            )
        ).one()
        db.delete(grant)
    db.add(bound)
    db.commit()
    admin_todos.dispatch_admin_todos(db, utc_now())
    assert not db.exec(
        select(InboxItem).where(
            InboxItem.org_id == p.org_id, InboxItem.kind == InboxKind.ADMIN_TODOS
        )
    ).all()


def test_digest_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b286001bc001")
    assert "admin_todo_date" not in {
        c["name"] for c in inspect(migration_engine).get_columns("inbox_item")
    }
    migrate(migration_engine, "upgrade", "head")
    assert {c["name"] for c in inspect(migration_engine).get_columns("inbox_item")} >= {
        "admin_todo_date",
        "admin_todo_count",
    }
    assert "uq_inbox_admin_todo_day" in {
        c["name"]
        for c in inspect(migration_engine).get_unique_constraints("inbox_item")
    }
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM alembic_version")
            ).scalar_one()
            == 1
        )
