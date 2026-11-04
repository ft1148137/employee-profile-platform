import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.core.db import engine
from app.domains.base import utc_now
from app.domains.bot import admission, store
from app.domains.bot.admission import (
    BotBusy,
    BotDraftingPriority,
    BotQuotaPaused,
    BotRateLimited,
    begin_turn,
)
from app.domains.bot.models import BotMessage, BotSession, BotSetting
from app.domains.model.client import ModelRateLimited
from app.domains.model.models import ModelSetting
from app.domains.model.pi_client import PiBusy
from app.domains.org.models import OrgMembership, Role, RoleGrant
from app.worker.tasks import cleanup_bot_messages, draft_initial
from tests.api.routes.test_employee_requests import principal_for
from tests.authz.test_tenant_access import headers
from tests.authz.test_tenant_access import tenant_data as tenant_data
from tests.utils.migrations import migrate


def test_storage_ciphertext_scope_and_history(demo: tuple, db: Session) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    session = store.create_session(db, p)
    assert session.id.version == 4
    message = store.append_message(db, session, "user", {"text": "合成私密经历"})
    db.commit()
    assert (
        "合成私密经历"
        not in db.exec(
            select(BotMessage.ciphertext).where(BotMessage.id == message.id)
        ).one()
    )
    assert store.list_messages(db, p, session.id)[0].payload == {"text": "合成私密经历"}
    assert "合成私密经历" in store.load_history(db, session)[0].text
    for wrong in (
        replace(p, employee_id=people["E01"].id),
        replace(p, org_id=uuid4()),
        replace(p, employee_id=None),
    ):
        for read in (store.get_session, store.list_messages):
            with pytest.raises(store.BotSessionNotFound):
                read(db, wrong, session.id)
    with pytest.raises(store.BotSessionNotFound):
        store.create_session(db, replace(p, employee_id=None))
    for _ in range(25):
        store.append_message(db, session, "assistant", "x" * 2000)
    db.commit()
    history = store.load_history(db, session)
    assert len(history) <= 20 and sum(len(m.text) for m in history) == 24000
    assert all(m.role == "assistant" for m in history)
    db.delete(session)
    db.commit()
    assert (
        db.exec(select(BotMessage).where(BotMessage.session_id == session.id)).all()
        == []
    )


@pytest.mark.parametrize("days", [0, 7])
def test_retention_and_no_resurrection(demo: tuple, db: Session, days: int) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    now = utc_now()
    db.add(BotSetting(org_id=p.org_id, retention_days=days))
    db.commit()
    session = store.create_session(db, p)
    old = store.append_message(db, session, "user", "过期合成消息")
    old.created_at = now - timedelta(days=8)
    recent = store.append_message(db, session, "assistant", "保留合成消息")
    session.last_activity_at = now - timedelta(hours=3)
    db.add_all([old, session])
    db.commit()
    assert len(store.list_messages(db, p, session.id)) == (1 if days else 0)
    if not days:
        # A new turn must not resurrect idle history before the scheduled sweep.
        store.append_message(db, session, "user", "新会话内容")
        db.commit()
        assert [m.payload for m in store.list_messages(db, p, session.id)] == [
            "新会话内容"
        ]
        session.last_activity_at = now - timedelta(hours=3)
        db.add(session)
        db.commit()
    store.cleanup_messages(db, now)
    remaining = db.exec(
        select(BotMessage).where(BotMessage.session_id == session.id)
    ).all()
    assert [m.id for m in remaining] == ([recent.id] if days else [])
    assert store.get_session(db, p, session.id).id == session.id
    cleanup_bot_messages(0)


def test_settings_validation_and_auth(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, people, auth = demo
    endpoint = url + "/bot-settings"
    assert client.get(endpoint, headers=auth["admin"]).json() == {
        "question_guide": "",
        "retention_days": 0,
    }
    for role in ("employee", "manager", "foreign"):
        assert client.get(endpoint, headers=auth[role]).status_code in (403, 404)
        assert client.put(endpoint, headers=auth[role], json={}).status_code in (
            403,
            404,
        )
    for invalid in (
        {"retention_days": -1},
        {"retention_days": 31},
        {"question_guide": "问" * 4001},
    ):
        assert (
            client.put(endpoint, headers=auth["admin"], json=invalid).status_code == 422
        )
    body = {"retention_days": 7, "question_guide": "请询问已经确认的经历"}
    assert client.put(endpoint, headers=auth["admin"], json=body).json() == body
    assert client.get(endpoint, headers=auth["admin"]).json() == body
    foreign_url = url.rsplit("/", 1)[0] + "/" + str(uuid4()) + "/bot-settings"
    assert client.put(foreign_url, headers=auth["admin"], json=body).status_code == 404
    assert (
        db.exec(
            select(BotSetting).where(BotSetting.org_id != people["E02"].org_id)
        ).all()
        == []
    )


def test_two_tenants_remain_isolated(
    tenant_data: tuple, client: TestClient, db: Session
) -> None:
    orgs, _, _, users = tenant_data
    p = get_org_principal(orgs[0].id, users[2], db)
    foreign = get_org_principal(orgs[1].id, users[3], db)
    a, b = store.create_session(db, p), store.create_session(db, foreign)
    store.append_message(db, a, "user", "企业甲的合成消息")
    store.append_message(db, b, "user", "企业乙的合成消息")
    db.commit()
    for principal, own, other in ((p, a, b), (foreign, b, a)):
        assert len(store.list_messages(db, principal, own.id)) == 1
        with pytest.raises(store.BotSessionNotFound):
            store.list_messages(db, principal, other.id)
        with pytest.raises(store.BotSessionNotFound):
            with begin_turn(db, principal, other, "禁止跨企业"):
                pytest.fail("cross-tenant turn")
    db.add(
        BotMessage(
            org_id=foreign.org_id, session_id=a.id, role="user", ciphertext="synthetic"
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == foreign.org_id, OrgMembership.user_id == users[3].id
        )
    ).one()
    db.add(
        RoleGrant(
            org_id=foreign.org_id, membership_id=membership.id, role=Role.ORG_ADMIN
        )
    )
    db.commit()
    left, right = [f"/api/v1/orgs/{o.id}/bot-settings" for o in orgs]
    assert (
        client.put(
            left, headers=headers(users[0]), json={"retention_days": 7}
        ).status_code
        == 200
    )
    assert (
        client.put(
            right, headers=headers(users[3]), json={"retention_days": 30}
        ).status_code
        == 200
    )
    assert client.get(left, headers=headers(users[0])).json()["retention_days"] == 7
    assert client.get(right, headers=headers(users[3])).json()["retention_days"] == 30
    assert client.get(left, headers=headers(users[3])).status_code == 404


def test_concurrent_leases_and_org_capacity(demo: tuple, db: Session) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    a = store.create_session(db, p)
    other = replace(p, employee_id=people["E01"].id)
    b = store.create_session(db, other)
    c = store.create_session(db, p)
    db.commit()
    ready, release = Barrier(3), Event()

    def hold(principal: admission.Principal, row: BotSession) -> None:
        with (
            Session(engine) as connection,
            begin_turn(connection, principal, row, "合成并发输入"),
        ):
            ready.wait(timeout=10)
            assert release.wait(10)

    started = time.monotonic()
    with ThreadPoolExecutor(2) as pool:
        for _ in range(8):
            release.clear()
            futures = [pool.submit(hold, p, a), pool.submit(hold, other, b)]
            try:
                ready.wait(timeout=10)
                with Session(engine) as connection:
                    for row in (a, c):
                        with pytest.raises(BotBusy):
                            with begin_turn(connection, p, row, "不得写入"):
                                pytest.fail("overlapping turn admitted")
                time.sleep(0.03)
            finally:
                release.set()
            for future in futures:
                future.result(timeout=10)
    logging.getLogger(__name__).warning(
        "Synthetic database load: batches=8 delay_ms=30 accepted=16 rejected=16 elapsed_ms=%d org_limit=2",
        round((time.monotonic() - started) * 1000),
    )
    db.expire_all()
    assert all(db.get(BotSession, r.id).lease_until is None for r in (a, b, c))
    assert len(store.list_messages(db, p, a.id)) == 8
    assert len(store.list_messages(db, other, b.id)) == 8
    assert not store.list_messages(db, p, c.id)


def test_hourly_limit_across_sessions_and_expiry(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    a, b = store.create_session(db, p), store.create_session(db, p)
    now = utc_now()
    monkeypatch.setattr(admission, "utc_now", lambda: now)
    for index in range(30):
        with begin_turn(db, p, a if index % 2 else b, "合成输入"):
            pass
    with pytest.raises(BotRateLimited) as error:
        with begin_turn(db, p, a, "第31轮"):
            pytest.fail("rate limit bypassed")
    assert (
        now + timedelta(minutes=59) < error.value.retry_at < now + timedelta(minutes=61)
    )
    assert db.get(BotSession, a.id).turn_count == 15
    monkeypatch.setattr(
        admission, "utc_now", lambda: now + timedelta(hours=1, minutes=1)
    )
    with begin_turn(db, p, a, "限频已恢复"):
        pass
    assert db.get(BotSession, a.id).turn_count == 16


def test_hourly_limit_race_uses_database(demo: tuple, db: Session) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    a, b = store.create_session(db, p), store.create_session(db, p)
    for _ in range(29):
        store.append_message(db, a, "user", "合成历史")
    db.commit()
    ready = Barrier(2)

    def attempt(row: BotSession) -> str:
        with Session(engine) as connection:
            ready.wait(timeout=10)
            try:
                with begin_turn(connection, p, row, "争抢第30轮"):
                    return "accepted"
            except BotRateLimited:
                return "limited"

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(attempt, row) for row in (a, b)]
        assert sorted(f.result(timeout=10) for f in futures) == ["accepted", "limited"]
    assert (
        len(store.list_messages(db, p, a.id)) + len(store.list_messages(db, p, b.id))
        == 30
    )


def test_expired_lease_successor_and_failure_release(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    row = store.create_session(db, p)
    row.lease_until = utc_now() - timedelta(seconds=1)
    db.add(row)
    db.commit()
    now = utc_now()
    with Session(engine) as first, Session(engine) as second:
        old = begin_turn(first, p, row, "前一轮")
        old.__enter__()
        monkeypatch.setattr(admission, "utc_now", lambda: now + timedelta(seconds=80))
        with begin_turn(second, p, row, "接管"):
            old.__exit__(None, None, None)
            second.expire_all()
            assert store.get_session(second, p, row.id).lease_until is not None
    for error in (RuntimeError("synthetic"), PiBusy("busy")):
        with pytest.raises(BotBusy if isinstance(error, PiBusy) else RuntimeError):
            with begin_turn(db, p, row, "异常轮"):
                store.append_message(db, row, "assistant", "未完成，不应保存")
                raise error
        db.expire_all()
        assert db.get(BotSession, row.id).lease_until is None
    assert all(
        m.payload != "未完成，不应保存" for m in store.list_messages(db, p, row.id)
    )


def test_quota_pause_and_drafting_priority(demo: tuple, db: Session) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    row = store.create_session(db, p)
    setting = ModelSetting(
        org_id=p.org_id, kind="cloud_api", updated_by_user_id=p.user_id
    )
    db.add(setting)
    db.commit()
    for retry in (None, utc_now() + timedelta(minutes=10)):
        setting.paused_until = None
        db.add(setting)
        db.commit()
        with pytest.raises(BotQuotaPaused) as error:
            with begin_turn(db, p, row, "额度不足"):
                raise ModelRateLimited(retry)
        assert error.value.estimated is (retry is None)
        with pytest.raises(BotQuotaPaused):
            with begin_turn(db, p, row, "共享暂停"):
                pytest.fail("quota pause ignored")
        db.refresh(row)
        assert row.lease_until is None
    setting.paused_until = utc_now() - timedelta(seconds=1)
    db.add(setting)
    job_id = draft_initial.configure(
        connection=db.connection().connection.driver_connection
    ).defer(org_id=str(p.org_id))
    db.execute(
        text("UPDATE procrastinate_jobs SET status='doing' WHERE id=:id"),
        {"id": job_id},
    )
    db.commit()
    try:
        with begin_turn(db, p, row, "API key 不受运行中的起草影响"):
            pass
        setting.kind = "chatgpt_subscription"
        db.add(setting)
        db.commit()
        with pytest.raises(BotDraftingPriority):
            with begin_turn(db, p, row, "订阅让位"):
                pytest.fail("drafting priority ignored")
        setting.kind = "cloud_api"
        db.add(setting)
        db.execute(
            text("UPDATE procrastinate_jobs SET status='todo' WHERE id=:id"),
            {"id": job_id},
        )
        db.commit()
        with pytest.raises(BotBusy):
            with begin_turn(db, p, row, "部署起草队列优先"):
                pytest.fail("draft queue ignored")
    finally:
        db.rollback()
        db.execute(text("DELETE FROM procrastinate_jobs WHERE id=:id"), {"id": job_id})
        db.commit()


def test_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b102001bc001")
    assert "bot_session" not in inspect(migration_engine).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    assert {"bot_session", "bot_message", "bot_setting"} <= set(
        inspect(migration_engine).get_table_names()
    )
    assert len(inspect(migration_engine).get_foreign_keys("bot_message")) == 2
    migrate(migration_engine, "downgrade", "b102001bc001")
    migrate(migration_engine, "upgrade", "head")
