from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.authz.principal import Principal
from app.domains.admin_assistant import core, store
from app.domains.admin_assistant.models import (
    AdminAssistantMessage,
)
from app.domains.admin_assistant.schemas import AdminTurnInput
from app.domains.base import utc_now
from app.domains.bot import admission
from app.domains.bot import store as messages
from app.domains.bot.admission import BotBusy, BotRateLimited, begin_turn
from app.domains.bot.models import BotSetting
from app.domains.model.client import (
    ModelRateLimited,
    ModelServiceError,
    ModelUnavailable,
)
from app.domains.model.models import ModelSetting
from app.domains.model.pi_client import PiBusy, TurnResult
from app.domains.org.models import OrgMembership, Role, RoleGrant
from app.models import User
from app.worker.tasks import draft_initial
from tests.api.routes.test_employee_requests import principal_for
from tests.domains.test_bot_core import enable
from tests.utils.migrations import migrate


def admin(db: Session, org_id: UUID) -> Principal:
    user = db.exec(select(User).where(User.email == "admin@demo.example.com")).one()
    return get_org_principal(org_id, user, db)


def test_turn_and_private_sessions(
    demo: tuple, db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, people, auth = demo
    org_id = people["E02"].org_id
    p = admin(db, org_id)
    assert p.employee_id is None
    query = {"org_id": str(org_id)}
    endpoint = "/api/v1/admin-assistant"
    sid = client.post(
        endpoint + "/sessions", headers=auth["admin"], params=query
    ).json()["session_id"]
    url = f"{endpoint}/sessions/{sid}"
    body = {"text": "今天有什么要处理的", "page": "/admin/people?tab=roster"}
    for role in ("employee", "manager", "foreign"):
        assert (
            client.get(url + "/messages", headers=auth[role], params=query).status_code
            == 404
        )
        assert (
            client.post(
                url + "/turns", headers=auth[role], params=query, json=body
            ).status_code
            == 404
        )
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.employee_id == people["E01"].id,
        )
    ).one()
    db.add(
        RoleGrant(
            org_id=org_id,
            membership_id=membership.id,
            role=Role.ORG_ADMIN,
            granted_by_user_id=p.user_id,
        )
    )
    db.commit()
    assert (
        client.get(url + "/messages", headers=auth["manager"], params=query).status_code
        == 404
    )
    assert (
        client.post(
            url + "/turns", headers=auth["manager"], params=query, json=body
        ).status_code
        == 404
    )
    assert (
        client.get(
            url + "/messages", headers=auth["admin"], params={"org_id": str(uuid4())}
        ).status_code
        == 404
    )

    def unavailable(*_args: object, **_kwargs: object) -> TurnResult:
        pytest.fail("unconfigured model must not call Pi")

    monkeypatch.setattr(core.pi_client, "run_turn", unavailable)
    assert (
        client.get(
            endpoint + "/availability", headers=auth["admin"], params=query
        ).json()["available"]
        is False
    )
    assert (
        "配置模型"
        in client.post(
            url + "/turns", headers=auth["admin"], params=query, json=body
        ).json()["messages"][0]["text"]
    )
    enable(db, org_id)
    expected = client.get(base + "/admin/overview", headers=auth["admin"]).json()

    def reply(_config: object, **kwargs: object) -> TurnResult:
        assert body["page"] in kwargs["system_prompt"]
        assert [t.name for t in kwargs["tools"]] == [
            "get_today",
            "list_admin_functions",
            "admin_read",
        ]
        assert kwargs["on_tool_call"]("get_today", {}) == {"data": expected}
        assert (
            "error"
            in kwargs["on_tool_call"]("get_today", {"org_id": str(org_id)})["data"]
        )
        assert "error" in kwargs["on_tool_call"]("write_settings", {})["data"]
        return TurnResult(
            text="请查看首页待办。", stop_reason="stop", model_calls=1, tool_calls=1
        )

    monkeypatch.setattr(core.pi_client, "run_turn", reply)
    for invalid in ({**body, "page": "/other"}, {**body, "org_id": str(uuid4())}):
        assert (
            client.post(
                url + "/turns", headers=auth["admin"], params=query, json=invalid
            ).status_code
            == 422
        )
    response = client.post(
        url + "/turns", headers=auth["admin"], params=query, json=body
    )
    assert response.status_code == 200
    assert response.json()["messages"][1] == {
        "type": "link",
        "title": "打开管理首页",
        "page": "/admin",
    }
    history = client.get(url + "/messages", headers=auth["admin"], params=query).json()
    assert history[0]["payload"] == body
    assert history[1]["payload"] == response.json()
    assert (
        body["text"]
        not in db.exec(
            select(AdminAssistantMessage.ciphertext).where(
                AdminAssistantMessage.session_id == UUID(sid)
            )
        ).first()
    )
    for wrong in (
        replace(p, user_id=uuid4()),
        replace(p, org_id=uuid4()),
        replace(p, is_org_admin=False),
    ):
        with pytest.raises(messages.BotSessionNotFound):
            store.get_session(db, wrong, UUID(sid))
    with pytest.raises(messages.BotSessionNotFound):
        store.create_session(db, replace(p, is_org_admin=False))


@pytest.mark.parametrize("days", [0, 7])
def test_retention_and_tenant_constraints(demo: tuple, db: Session, days: int) -> None:
    _, people, _ = demo
    p = admin(db, people["E02"].org_id)
    row = store.create_session(db, p)
    now = utc_now()
    db.add(BotSetting(org_id=p.org_id, retention_days=days))
    old = messages.append_message(db, row, "user", {"text": "过期内容"})
    old.created_at = now - timedelta(days=8)
    recent = messages.append_message(db, row, "assistant", {"text": "保留内容"})
    row.last_activity_at = now - timedelta(hours=3)
    db.add_all([old, row])
    db.commit()
    if not days:
        assert messages.load_history(db, row) == []
        messages.append_message(db, row, "user", {"text": "新的内容"})
        db.commit()
        assert len(messages.messages_for(db, row)) == 1
        row.last_activity_at = now - timedelta(hours=3)
        db.add(row)
        db.commit()
    messages.cleanup_messages(db, now)
    remaining = db.exec(
        select(AdminAssistantMessage).where(AdminAssistantMessage.session_id == row.id)
    ).all()
    assert [m.id for m in remaining] == ([recent.id] if days else [])
    assert len(messages.load_history(db, row)) == (1 if days else 0)
    db.add(
        AdminAssistantMessage(
            org_id=uuid4(), session_id=row.id, role="user", ciphertext="synthetic"
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.delete(row)
    db.commit()
    assert (
        db.exec(
            select(AdminAssistantMessage).where(
                AdminAssistantMessage.session_id == row.id
            )
        ).all()
        == []
    )


@pytest.mark.parametrize("admin_first", [True, False])
def test_shared_admission(demo: tuple, db: Session, admin_first: bool) -> None:
    _, people, _ = demo
    p = admin(db, people["E02"].org_id)
    employee = principal_for(db, p.org_id)
    a = store.create_session(db, p)
    b = messages.create_session(db, employee)
    c = (
        store.create_session(db, p)
        if admin_first
        else messages.create_session(db, employee)
    )
    first, second = ((p, a), (employee, b)) if admin_first else ((employee, b), (p, a))
    with begin_turn(db, *first, "第一轮"):
        with begin_turn(db, *second, "第二轮"):
            with pytest.raises(BotBusy):
                with begin_turn(db, first[0], c, "名额已满"):
                    pytest.fail("shared limit bypassed")
    db.refresh(a)
    db.refresh(b)
    assert a.lease_until is b.lease_until is None
    with begin_turn(db, p, a, "已恢复"):
        with pytest.raises(BotBusy):
            with begin_turn(db, p, a, "重复轮次"):
                pytest.fail("session lease bypassed")


def test_admin_hourly_limit_and_availability(demo: tuple, db: Session) -> None:
    _, people, _ = demo
    p = admin(db, people["E02"].org_id)
    a, b = store.create_session(db, p), store.create_session(db, p)
    for _ in range(admission.HOURLY_TURNS):
        messages.append_message(db, a, "user", {"text": "合成输入"})
    db.commit()
    with pytest.raises(BotRateLimited):
        with begin_turn(db, p, b, "超限"):
            pytest.fail("hourly limit bypassed")
    enable(db, p.org_id)
    setting = db.get(ModelSetting, p.org_id)
    setting.enabled = False
    db.add(setting)
    db.commit()
    assert "尚未启用" in core.availability(db, p).reason
    setting.enabled = True
    setting.paused_until = utc_now() + timedelta(minutes=5)
    db.add(setting)
    db.commit()
    assert "额度" in core.availability(db, p).reason


@pytest.mark.parametrize(
    "error",
    [
        ModelUnavailable(),
        admission.BotDraftingPriority(),
        PiBusy(),
        ModelRateLimited(None),
        ModelServiceError("upstream_unavailable"),
        None,
    ],
)
def test_failed_turn_releases_lease(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch, error: Exception | None
) -> None:
    _, people, _ = demo
    p = admin(db, people["E02"].org_id)
    enable(db, p.org_id)
    row = store.create_session(db, p)

    def fail(*_args: object, **_kwargs: object) -> TurnResult:
        if error:
            raise error
        return TurnResult(
            text="未完成", stop_reason="length", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(core.pi_client, "run_turn", fail)
    response = core.turn(
        db,
        p,
        row.id,
        AdminTurnInput(text="查看员工", page="/admin/people/$employeeId"),
    )
    assert (
        response.messages[0].type == "text"
        and "未完成" not in response.messages[0].text
    )
    assert response.messages[1].type == "link"
    assert response.messages[1].page == "/admin/people"
    if isinstance(error, admission.BotDraftingPriority):
        assert "正在起草画像" in response.messages[0].text
    db.refresh(row)
    assert row.lease_until is None
    assert len(messages.messages_for(db, row)) == 2


def test_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b129001bc001")
    assert "admin_assistant_session" not in inspect(migration_engine).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    assert (
        len(inspect(migration_engine).get_foreign_keys("admin_assistant_session")) == 2
    )
    assert (
        len(inspect(migration_engine).get_foreign_keys("admin_assistant_message")) == 2
    )


@pytest.mark.parametrize(
    "function", ["get_progress", "list_accounts", "update_approval_policy", "missing"]
)
def test_registry_wired_to_turn(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch, function: str
) -> None:
    _, people, _ = demo
    p = admin(db, people["E02"].org_id)
    enable(db, p.org_id)
    row = store.create_session(db, p)

    def reply(_config: object, **kwargs: object) -> TurnResult:
        assert "list_accounts" in kwargs["system_prompt"]
        callback = kwargs["on_tool_call"]
        assert callback("list_admin_functions", {"area": "people"})["data"]
        data = callback("admin_read", {"function": function, "args": {}})
        assert set(data) == {"data"}
        if function in {"missing", "update_approval_policy"}:
            assert "只读" in data["data"]["error"]
        return TurnResult(
            text="查询完成", stop_reason="stop", model_calls=1, tool_calls=2
        )

    monkeypatch.setattr(core.pi_client, "run_turn", reply)
    response = core.turn(
        db, p, row.id, AdminTurnInput(text="同步怎么样了", page="/admin")
    )
    cards = response.messages[1:]
    if function in {"get_progress", "list_accounts"}:
        assert cards[0].type == "link"
        assert cards[0].page == (
            "/admin" if function == "get_progress" else "/admin/people?tab=accounts"
        )
        assert cards[1].type == "table" and len(cards[1].rows) <= 20
    else:
        assert cards == []


@pytest.mark.parametrize(
    "kind,status,available",
    [
        ("cloud_api", "doing", True),
        ("chatgpt_subscription", "doing", False),
        ("cloud_api", "todo", False),
    ],
)
def test_drafting_unavailable(
    demo: tuple,
    db: Session,
    client: TestClient,
    kind: str,
    status: str,
    available: bool,
) -> None:
    _, people, auth = demo
    p = admin(db, people["E02"].org_id)
    enable(db, p.org_id)
    setting = db.get(ModelSetting, p.org_id)
    setting.kind = kind
    db.add(setting)
    job_id = draft_initial.configure(
        connection=db.connection().connection.driver_connection
    ).defer(org_id=str(p.org_id))
    db.execute(
        text("UPDATE procrastinate_jobs SET status=:status WHERE id=:id"),
        {"status": status, "id": job_id},
    )
    db.commit()
    try:
        result = client.get(
            "/api/v1/admin-assistant/availability",
            params={"org_id": str(p.org_id)},
            headers=auth["admin"],
        ).json()
        assert result["available"] is available
        if not available:
            assert "正在起草画像" in result["reason"]
            row = store.create_session(db, p)
            response = core.turn(
                db,
                p,
                row.id,
                AdminTurnInput(text="你好", page="/admin/data-ai?tab=sources"),
            )
            assert response.messages[1].type == "link"
            assert response.messages[1].page == "/admin/data-ai?tab=sources"
    finally:
        db.rollback()
        db.execute(text("DELETE FROM procrastinate_jobs WHERE id=:id"), {"id": job_id})
        db.commit()
