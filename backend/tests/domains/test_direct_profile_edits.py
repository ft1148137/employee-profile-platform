import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, select

from app.core.db import engine, init_db
from app.domains.audit.models import AuditEvent
from app.domains.change_request.models import (
    ChangeItem,
    ProfileChangeRequest,
    ReviewDecision,
)
from app.domains.evidence.models import Evidence
from app.domains.im import notifications
from app.domains.im.models import ImBinding, ImNotification, ImSetting
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.review import direct, service
from app.domains.review.publish import current_claims
from tests.api.routes.test_direct_profile_edits import edit_body
from tests.api.routes.test_reviews import actor, pending, values
from tests.utils.migrations import migrate


def test_notification_uses_beijing_time_and_request_stays_utc(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 11, 12, 2, tzinfo=UTC)
    monkeypatch.setattr(direct, "utc_now", lambda: now)
    subject = demo[1]["E02"]
    direct.direct_edit(
        actor(db, subject.org_id, "manager.platform@demo.example.com"),
        db,
        subject.id,
        direct.DirectEditInput.model_validate(edit_body(db, subject)),
        via="web",
        client_name="timezone-test",
    )
    item = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == subject.org_id,
            InboxItem.recipient_employee_id == subject.id,
            InboxItem.kind == InboxKind.PROFILE_EDITED,
        )
    ).one()
    assert "2026-11-12 10:00" in item.message
    request = db.get(ProfileChangeRequest, item.request_id)
    assert request is not None and request.decided_at is not None
    assert request.submitted_at.isoformat() == now.isoformat()
    assert request.decided_at.isoformat() == now.isoformat()


def test_direct_publication_audit_retract_and_atomicity(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = demo[1]["E02"]
    admin = actor(db, subject.org_id, "admin@demo.example.com")
    body = direct.DirectEditInput.model_validate(edit_body(db, subject))
    before_evidence = {
        e.id: e.model_dump(mode="json")
        for e in db.exec(
            select(Evidence).where(Evidence.org_id == subject.org_id)
        ).all()
    }
    before_inbox = set(
        db.exec(select(InboxItem.id).where(InboxItem.org_id == subject.org_id)).all()
    )
    preview = direct.preview_direct_edit(admin, db, subject.id, body)
    assert preview.items[0].before.statement
    result = direct.direct_edit(
        admin, db, subject.id, body, via="web", client_name="合成入口", commit=False
    )
    db.rollback()
    assert edit_body(db, subject)["base_revision"] == body.base_revision
    result = direct.direct_edit(
        admin, db, subject.id, body, via="web", client_name="合成入口"
    )
    assert result.revision == body.base_revision + 1
    request = db.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == subject.org_id,
            ProfileChangeRequest.client_name == "合成入口",
        )
    ).one()
    assert request.status == "approved" and request.origin == "manual"
    assert request.author["user_id"] == str(admin.user_id)
    decision = db.exec(
        select(ReviewDecision).where(
            ReviewDecision.org_id == subject.org_id,
            ReviewDecision.request_id == request.id,
        )
    ).one()
    assert decision.via == "direct" and decision.reviewer_user_id == admin.user_id
    item = db.exec(
        select(ChangeItem).where(
            ChangeItem.org_id == subject.org_id, ChangeItem.request_id == request.id
        )
    ).one()
    assert item.decision == "approved" and item.published_claim_id
    inbox = [
        i
        for i in db.exec(
            select(InboxItem).where(InboxItem.org_id == subject.org_id)
        ).all()
        if i.id not in before_inbox
    ]
    assert len(inbox) == 1 and inbox[0].kind == InboxKind.PROFILE_EDITED
    assert inbox[0].recipient_employee_id == subject.id
    assert (
        "直接修改了你的画像" in inbox[0].message
        and result.items[0].capability_name in inbox[0].message
    )
    assert (
        body.items[0].statement not in inbox[0].message
        and "routine" not in inbox[0].message
    )
    assert not db.exec(
        select(ImNotification).where(
            ImNotification.org_id == subject.org_id,
            ImNotification.inbox_item_id == inbox[0].id,
        )
    ).all()
    audit = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == subject.org_id,
            AuditEvent.action == "profile.direct_edited",
        )
    ).one()
    assert audit.actor_id == str(admin.user_id) and audit.details["role"] == "org_admin"
    assert audit.details["items"] == [i.model_dump(mode="json") for i in result.items]
    public_text = (
        result.model_dump_json()
        + preview.model_dump_json()
        + json.dumps(audit.details, ensure_ascii=False)
    )
    private = [
        e["content"][key]
        for e in before_evidence.values()
        if e["subject_employee_id"] == str(subject.id)
        for key in ("text", "comment")
        if e["content"].get(key)
    ]
    assert len(private) >= 2 and all(text not in public_text for text in private)
    assert set(result.items[0].before.model_dump()) == {
        "statement",
        "level",
        "assessment_status",
    }
    body.base_revision = result.revision
    body.items[0].op = "retract"
    retracted = direct.direct_edit(
        admin, db, subject.id, body, via="bot", client_name="合成助手"
    )
    assert retracted.items[0].after.assessment_status == "unknown"
    body.base_revision = retracted.revision
    body.items[0] = direct.DirectItemInput(
        op="add", capability_key="automated_testing", statement="合成新增"
    )

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(direct, "record_audit", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        direct.direct_edit(
            admin, db, subject.id, body, via="web", client_name="合成入口"
        )
    db.rollback()
    assert "automated_testing" not in current_claims(db, subject.org_id, subject.id)
    assert before_evidence == {
        e.id: e.model_dump(mode="json")
        for e in db.exec(
            select(Evidence).where(Evidence.org_id == subject.org_id)
        ).all()
    }


@pytest.mark.parametrize(
    "problem", ["duplicate", "level", "key", "add_existing", "modify_absent"]
)
def test_invalid_items_write_nothing(demo: tuple, db: Session, problem: str) -> None:
    subject = demo[1]["E02"]
    body = edit_body(db, subject)
    item = body["items"][0]
    if problem == "duplicate":
        body["items"].append(item.copy())
    elif problem == "level":
        item["level"] = "invalid"
    elif problem == "key":
        item["capability_key"] = "invalid"
    elif problem == "add_existing":
        item["op"] = "add"
    else:
        item["capability_key"] = "technical_docs"
    with pytest.raises(HTTPException) as error:
        direct.direct_edit(
            actor(db, subject.org_id, "admin@demo.example.com"),
            db,
            subject.id,
            direct.DirectEditInput.model_validate(body),
            via="web",
            client_name="invalid",
        )
    assert error.value.status_code == 422
    assert not db.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == subject.org_id,
            ProfileChangeRequest.client_name == "invalid",
        )
    ).all()


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"op": "attach_evidence", "capability_key": "x", "statement": "x"}],
        [{"op": "add", "capability_key": "x", "statement": ""}],
        [{"op": "add", "capability_key": "x", "statement": "x"}] * 51,
    ],
)
def test_input_limits(items: list[dict]) -> None:
    with pytest.raises(ValidationError):
        direct.DirectEditInput(items=items)


def test_add_without_previous_version_and_pending_request_conflict(
    demo: tuple, db: Session
) -> None:
    owner, request = pending(db, demo)
    subject = demo[1]["E02"]
    manager = actor(db, subject.org_id, "manager.platform@demo.example.com")
    direct.direct_edit(
        manager,
        db,
        subject.id,
        direct.DirectEditInput.model_validate(edit_body(db, subject)),
        via="bot",
        client_name="经理助手",
    )
    with pytest.raises(HTTPException, match="目标判断已被其他请求取代"):
        service.decide(manager, db, request.id, request.revision, values(request))
    db.rollback()
    from app.domains.org.models import Employee

    empty = Employee(
        org_id=subject.org_id,
        department_id=subject.department_id,
        display_name="合成新员工",
    )
    db.add(empty)
    db.commit()
    result = direct.direct_edit(
        manager,
        db,
        empty.id,
        direct.DirectEditInput(
            items=[
                direct.DirectItemInput(
                    op="add",
                    capability_key="backend_api",
                    statement="合成新增",
                    level=None,
                )
            ]
        ),
        via="bot",
        client_name="经理助手",
    )
    assert result.revision == 1 and result.items[0].before is None
    assert result.items[0].after.level is None


@pytest.mark.parametrize("notify", [True, False])
def test_feishu_copy_and_opt_out(demo: tuple, db: Session, notify: bool) -> None:
    subject = demo[1]["E02"]
    manager = actor(db, subject.org_id, "manager.platform@demo.example.com")
    setting = ImSetting(
        org_id=subject.org_id,
        platform="feishu",
        enabled=True,
        credentials="synthetic",
        bot_identity="synthetic",
        updated_by_user_id=manager.user_id,
    )
    db.add(setting)
    db.add(
        ImBinding(
            org_id=subject.org_id,
            employee_id=subject.id,
            platform="feishu",
            notify=notify,
            external_user_id="synthetic",
            external_chat_id="synthetic",
            bot_identity="synthetic",
        )
    )
    db.commit()
    direct.direct_edit(
        manager,
        db,
        subject.id,
        direct.DirectEditInput.model_validate(edit_body(db, subject)),
        via="bot",
        client_name="经理助手",
    )
    item = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == subject.org_id,
            InboxItem.kind == InboxKind.PROFILE_EDITED,
        )
    ).one()
    queued = db.exec(
        select(ImNotification).where(
            ImNotification.org_id == subject.org_id,
            ImNotification.inbox_item_id == item.id,
        )
    ).all()
    assert len(queued) == int(notify)
    adapter = AsyncMock()
    asyncio.run(
        notifications.send_pending(
            engine, {(subject.org_id, "feishu"): (setting.credentials, adapter)}
        )
    )
    if notify:
        adapter.send_text.assert_awaited_once_with("synthetic", item.message)
        assert "routine" not in item.message
    else:
        adapter.send_text.assert_not_awaited()


def test_direct_migration_roundtrip(migration_engine: Engine) -> None:
    from app.core.config import settings
    from app.domains.org.models import Employee
    from app.seed.loader import load_demo

    with Session(migration_engine) as db:
        init_db(db)
        db.commit()
        load_demo(db, settings.DEMO_DATA_DIR)
        subject = db.exec(select(Employee).where(Employee.employee_code == "E02")).one()
        direct.direct_edit(
            actor(db, subject.org_id, "admin@demo.example.com"),
            db,
            subject.id,
            direct.DirectEditInput.model_validate(edit_body(db, subject)),
            via="web",
            client_name="migration",
        )
    migrate(migration_engine, "downgrade", "b368001ss001")
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT via FROM review_decision WHERE client_name='migration'")
            ).scalar_one()
            == "web"
        )
        assert (
            connection.execute(
                text(
                    "SELECT kind FROM inbox_item WHERE message LIKE '%直接修改了你的画像%'"
                )
            ).scalar_one()
            == "status_changed"
        )
    migrate(migration_engine, "upgrade", "head")
    for table, constraint, value in [
        ("review_decision", "review_decision_via_check", "direct"),
        ("inbox_item", "inbox_item_kind_check", "profile_edited"),
    ]:
        checks = {
            c["name"]: c["sqltext"]
            for c in inspect(migration_engine).get_check_constraints(table)
        }
        assert value in checks[constraint]
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM alembic_version")
            ).scalar_one()
            == 1
        )


def test_concurrent_direct_edits_publish_once(demo: tuple, db: Session) -> None:
    subject = demo[1]["E02"]
    admin = actor(db, subject.org_id, "admin@demo.example.com")
    subject_id = subject.id
    body = direct.DirectEditInput.model_validate(edit_body(db, subject))
    db.rollback()
    barrier = Barrier(2)

    def run(_index: int) -> int:
        with Session(engine) as session:
            barrier.wait(timeout=10)
            try:
                direct.direct_edit(
                    admin,
                    session,
                    subject_id,
                    body,
                    via="web",
                    client_name="concurrent",
                )
                return 200
            except HTTPException as error:
                session.rollback()
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, range(2))) == [200, 409]
    assert (
        len(
            db.exec(
                select(ProfileChangeRequest).where(
                    ProfileChangeRequest.org_id == admin.org_id,
                    ProfileChangeRequest.client_name == "concurrent",
                )
            ).all()
        )
        == 1
    )
