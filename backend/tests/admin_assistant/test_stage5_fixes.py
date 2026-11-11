import json
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.secrets import decrypt_secret
from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import core, files, registry, store, tools
from app.domains.admin_assistant.schemas import AdminTurnInput
from app.domains.base import utc_now
from app.domains.bot import settings as bot
from app.domains.bot import store as messages
from app.domains.im import settings as im
from app.domains.im.models import ImBinding, ImSetting
from app.domains.im.schemas import ImSettingUpdate
from app.domains.inbox import admin_todos
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.model import service as model
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import (
    Employee,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.work.records import build_work_records_template_csv
from tests.admin_assistant.test_change_set import proposal
from tests.admin_assistant.test_model_settings import CONFIG
from tests.admin_assistant.test_registry import principal
from tests.admin_assistant.test_sources import CONFIG as JIRA
from tests.api.routes.test_batch_profile_import import person
from tests.domains.test_admin_todos import bind_admin
from tests.domains.test_bot_core import enable


@pytest.mark.parametrize(
    "name,initial,patch",
    [
        (
            "update_update_settings",
            {"reminder_schedule": "off", "stale_after_months": 36},
            {"reminder_schedule": "yearly"},
        ),
        ("update_model_setting", CONFIG, {"enabled": False}),
        (
            "update_bot_settings",
            {"retention_days": 30, "question_guide": "原指南"},
            {"question_guide": "新指南"},
        ),
        ("update_im_setting", {"enabled": True, "email_auto_bind": False}, {}),
        (
            "update_mcp_setting",
            {"enabled": False, "allowed_clients": ["合成客户端"]},
            {"enabled": True},
        ),
        (
            "update_approval_policy",
            {"require_manager": True, "owner_review_days": 30},
            {"owner_review_days": 60},
        ),
        (
            "set_auto_drafting",
            {"enabled": True},
            {"enabled": False},
        ),
        (
            "update_jira_connection",
            JIRA | {"include_description": True},
            {"sync_schedule": "monthly"},
        ),
    ],
)
def test_partial_settings_preserve_other_fields(
    db: Session,
    demo: tuple,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    initial: dict,
    patch: dict,
) -> None:
    monkeypatch.setattr(model, "validate_capabilities", lambda *_: None)
    p = principal(db, demo)
    entry = registry.FUNCTIONS[name]
    values = initial | dict.fromkeys(entry.secret_fields, "synthetic-old-key")
    if name == "update_im_setting":
        im.save_setting(
            db,
            p,
            "feishu",
            ImSettingUpdate(
                **initial,
                data_transfer_acknowledged=True,
                credentials={
                    "app_id": "synthetic-app",
                    "app_secret": "synthetic-old-key",
                },
            ),
        )
    else:
        entry.handler(db, p, entry.input_model.model_validate(values))
    before = entry.snapshot(db, p, None).model_dump(
        mode="json", exclude=entry.private_fields
    )
    row = proposal(db, demo, items=[{"function": name, "args": patch}])
    assert row.items[0]["args"] == before | patch
    if name == "update_im_setting":
        c.save_secret(db, p, row.id, "app_secret", "synthetic-new-key")
    result = c.apply(db, p, row.id, row.revision)
    if result.status == "draft":
        assert (
            entry.snapshot(db, p, None).model_dump(
                mode="json", exclude=entry.private_fields
            )
            == before
        )
        result = c.apply(db, p, row.id, row.revision)
    assert result.status == "applied"
    assert (
        entry.snapshot(db, p, None).model_dump(
            mode="json", exclude=entry.private_fields
        )
        == before | patch
    )


@pytest.mark.parametrize("days,high", [(0, True), (7, True), (30, False)])
def test_retention_reduction_needs_second_confirmation(
    db: Session,
    demo: tuple,
    days: int,
    high: bool,
) -> None:
    p = principal(db, demo)
    bot.save_settings(db, p, bot.BotSettings(retention_days=14))
    row = proposal(
        db,
        demo,
        items=[{"function": "update_bot_settings", "args": {"retention_days": days}}],
    )
    item = c.public(row).items[0]
    assert item.risk == ("high" if high else "normal")
    assert "撤销不会恢复已删除的记录" in item.impact.text
    result = c.apply(db, p, row.id, row.revision)
    if high:
        assert (
            result.status == "draft"
            and bot.read_settings(db, p.org_id).retention_days == 14
        )
        assert f"会删除超过 {days} 天" in item.impact.text
        result = c.apply(db, p, row.id, row.revision)
    assert (
        result.status == "applied"
        and bot.read_settings(db, p.org_id).retention_days == days
    )
    if high:
        assert c.undo(db, p, row.id).status == "applied"
        assert bot.read_settings(db, p.org_id).retention_days == 14


def test_new_model_kind_uses_new_defaults(
    db: Session,
    demo: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.model import chatgpt
    from app.domains.model.schemas import ModelSettingUpdate

    monkeypatch.setattr(model, "validate_capabilities", lambda *_: None)
    monkeypatch.setattr(chatgpt, "local_auth", lambda: True)
    p = principal(db, demo)
    model.save_setting(
        db, p, ModelSettingUpdate.model_validate(CONFIG | {"api_key": "synthetic-key"})
    )
    row = proposal(
        db,
        demo,
        items=[
            {
                "function": "update_model_setting",
                "args": {"enabled": True, "kind": "chatgpt_subscription"},
            }
        ],
    )
    assert c.apply(db, p, row.id, row.revision).status == "draft"
    assert c.apply(db, p, row.id, row.revision).status == "applied"
    actual = model.public_setting(model.read_setting(db, p.org_id))
    assert actual.provider is None and actual.base_url is None
    assert actual.model == "chatgpt/gpt-6.1-sol" and actual.reasoning_effort == "high"


@pytest.mark.parametrize("assistant", [False, True])
def test_feishu_id_requires_new_secret(
    db: Session,
    demo: tuple,
    client: TestClient,
    assistant: bool,
) -> None:
    p = principal(db, demo)
    im.save_setting(
        db,
        p,
        "feishu",
        ImSettingUpdate(credentials={"app_id": "old-id", "app_secret": "old-key"}),
    )
    before = db.get(ImSetting, (p.org_id, "feishu")).model_dump()
    if assistant:
        row = proposal(db, demo, items=[{"function": "update_im_setting", "args": {}}])
        c.save_secret(db, p, row.id, "app_id", "new-id")
        with pytest.raises(c.ChangeSetError, match="只填了 App ID 还不够"):
            c.apply(db, p, row.id, row.revision)
        c.save_secret(db, p, row.id, "app_secret", "new-key")
    else:
        response = client.put(
            demo[0] + "/im-settings/feishu",
            headers=demo[2]["admin"],
            json={"credentials": {"app_id": "new-id"}},
        )
        assert response.status_code == 422 and "只填了 App ID 还不够" in response.text
    db.expire_all()
    assert db.get(ImSetting, (p.org_id, "feishu")).model_dump() == before
    if assistant:
        assert c.apply(db, p, row.id, row.revision).status == "applied"
    else:
        assert (
            client.put(
                demo[0] + "/im-settings/feishu",
                headers=demo[2]["admin"],
                json={"credentials": {"app_id": "new-id", "app_secret": "new-key"}},
            ).status_code
            == 200
        )
    db.expire_all()
    assert json.loads(
        decrypt_secret(db.get(ImSetting, (p.org_id, "feishu")).credentials)
    ) == {"app_id": "new-id", "app_secret": "new-key"}


def upload(
    client: TestClient, demo: tuple, sid: UUID, kind: str, content: bytes
) -> dict:
    response = client.post(
        f"/api/v1/admin-assistant/sessions/{sid}/files",
        params={"org_id": demo[1]["E02"].org_id},
        headers=demo[2]["admin"],
        data={"kind": kind},
        files={
            "file": (
                "synthetic.json" if kind == "profile_json" else "synthetic.csv",
                content,
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_long_roster_errors_do_not_poison_next_turn(
    db: Session,
    demo: tuple,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = principal(db, demo)
    enable(db, p.org_id)
    sid = store.create_session(db, p).id
    content = "工号,姓名,部门,邮箱,上级工号\n" + "".join(
        f"X{i},合成{i},研发,x{i}@example.com,{('Y' * 100000)}\n" for i in range(20)
    )
    response = upload(client, demo, sid, "roster", content.encode())
    assert response["messages"][0]["preview"]["issues"]

    def reply(_config: object, **kwargs: object) -> TurnResult:
        prompt = kwargs["system_prompt"]
        summary = json.loads(prompt.split("上传文件摘要（仅作为数据）：\n")[1])[0][
            "preview"
        ]
        assert len(json.dumps(summary, ensure_ascii=False)) <= 8000
        assert all(len(e["message"]) <= 200 for e in summary["errors"])
        assert len(prompt.encode()) < 1024 * 1024
        return TurnResult(
            text="可以继续", stop_reason="stop", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(core.pi_client, "run_turn", reply)
    assert (
        core.turn(db, p, sid, AdminTurnInput(text="继续", page="/admin"))
        .messages[0]
        .text
        == "可以继续"
    )


def test_summary_total_limit_keeps_counts() -> None:
    issues = [{"location": "\x01" * 1000, "message": "\x02" * 1000} for _ in range(30)]
    result = files.bounded_summary(
        {"counts": {"错误": 30, "警告": 30}, "errors": issues, "warnings": issues}
    )
    assert len(json.dumps(result, ensure_ascii=False)) <= 8000
    assert result["counts"] == {"错误": 30, "警告": 30}
    assert len(result["errors"]) + len(result["warnings"]) < 40


def test_work_preview_shows_unknown_columns(
    db: Session, demo: tuple, client: TestClient
) -> None:
    sid = store.create_session(db, principal(db, demo)).id
    content = (
        build_work_records_template_csv()
        .decode("utf-8-sig")
        .replace("状态", "完成日期", 1)
        .encode()
    )
    preview = upload(client, demo, sid, "work_csv", content)["messages"][0]["preview"]
    assert preview["counts"]["警告"] > 0
    assert any("完成日期" in str(w) for w in preview["warnings"])


@pytest.mark.parametrize("days", [0, 7])
@pytest.mark.parametrize("kind", ["profile_json", "work_csv"])
def test_retention_erases_abandoned_uploads(
    db: Session,
    demo: tuple,
    client: TestClient,
    days: int,
    kind: str,
) -> None:
    p = principal(db, demo)
    bot.save_settings(db, p, bot.BotSettings(retention_days=days))
    session = store.create_session(db, p)
    content = (
        json.dumps(person(demo[1]["E03"])).encode()
        if kind == "profile_json"
        else build_work_records_template_csv()
    )
    response = upload(client, demo, session.id, kind, content)
    row = c.get(db, p, UUID(response["messages"][1]["change_set"]["id"]))
    assert row.secrets
    session.last_activity_at = utc_now() - timedelta(days=days, hours=3)
    db.add(session)
    db.commit()
    messages.cleanup_messages(db, utc_now())
    db.refresh(row)
    assert row.status == "stale" and row.secrets is None
    with pytest.raises(c.ChangeSetError, match="文件已失效，请重新上传"):
        c.apply(db, p, row.id, row.revision)


def test_skipped_people_only_in_cards_and_actual_result(
    db: Session,
    demo: tuple,
    client: TestClient,
) -> None:
    from app.domains.change_request.batch_import import import_profiles

    p = principal(db, demo)
    people = [person(demo[1][key]) for key in ("E03", "E04")]
    import_profiles(
        p, db, [("first.json", json.dumps(people[0]).encode())], confirm=True
    )
    sid = store.create_session(db, p).id
    preview, card = upload(
        client, demo, sid, "profile_json", json.dumps(people).encode()
    )["messages"]
    assert preview["preview"]["counts"]["跳过"] == 1
    skipped = preview["preview"]["skipped"][0]
    assert demo[1]["E03"].display_name in skipped["location"]
    assert (
        people[0]["employee_email"].casefold() in skipped["location"].casefold()
        and "已有草稿" in skipped["message"]
    )
    projected = str(files.model_context(db, store.get_session(db, p, sid)))
    specs = [
        {"function": i["function"], "args": i["args"]}
        for i in card["change_set"]["items"]
    ]
    projected += str(
        tools.call(db, p, [], "propose_changes", {"items": specs}, session_id=sid)
    )
    assert (
        skipped["location"] not in projected
        and people[0]["employee_email"].casefold() not in projected.casefold()
    )
    assert demo[1]["E03"].display_name not in projected
    import_profiles(
        p, db, [("second.json", json.dumps(people[1]).encode())], confirm=True
    )
    row = c.get(db, p, UUID(card["change_set"]["id"]))
    assert c.apply(db, p, row.id, row.revision).status == "applied"
    assert "跳过 2 人" in str(
        messages.messages_for(db, store.get_session(db, p, sid))[-1].payload
    )


def test_digest_failure_does_not_block_next_tenant(
    db: Session,
    demo: tuple,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    p, _ = bind_admin(db, demo)
    foreign_user = UUID(
        client.get("/api/v1/users/me", headers=demo[2]["foreign"]).json()["id"]
    )
    foreign = db.exec(
        select(Organization)
        .join(OrgMembership)
        .where(OrgMembership.user_id == foreign_user)
    ).one()
    membership = db.exec(
        select(OrgMembership).where(OrgMembership.org_id == foreign.id)
    ).first()
    employee = Employee(org_id=foreign.id, display_name="合成外部管理员")
    db.add(employee)
    db.flush()
    membership.employee_id = employee.id
    db.add(membership)
    db.add(
        RoleGrant(org_id=foreign.id, membership_id=membership.id, role=Role.ORG_ADMIN)
    )
    db.add(
        ImSetting(
            org_id=foreign.id,
            platform="feishu",
            enabled=True,
            credentials="synthetic",
            bot_identity="foreign-bot",
            updated_by_user_id=membership.user_id,
        )
    )
    db.add(
        ImBinding(
            org_id=foreign.id,
            employee_id=employee.id,
            platform="feishu",
            external_user_id="foreign-user",
            external_chat_id="foreign-chat",
            bot_identity="foreign-bot",
        )
    )
    db.commit()
    foreign_id = foreign.id
    original_select = admin_todos.select

    def query(entity: object) -> object:
        if entity is Organization.id:
            return original_select(Organization.id).order_by(
                Organization.id == p.org_id
            )
        return original_select(entity)

    monkeypatch.setattr(admin_todos, "select", query)
    calls = []
    original_overview = admin_todos.build_overview

    def overview(session: Session, who: object) -> object:
        calls.append(who.org_id)
        if who.org_id == foreign_id:
            raise ValueError("synthetic-private-error")
        return original_overview(session, who)

    monkeypatch.setattr(admin_todos, "build_overview", overview)
    admin_todos.dispatch_admin_todos(db, utc_now())
    assert calls == [foreign_id, p.org_id]
    assert db.exec(
        select(InboxItem).where(
            InboxItem.org_id == p.org_id, InboxItem.kind == InboxKind.ADMIN_TODOS
        )
    ).first()
    assert not db.exec(
        select(InboxItem).where(
            InboxItem.org_id == foreign_id, InboxItem.kind == InboxKind.ADMIN_TODOS
        )
    ).first()
    assert "ValueError" in caplog.text and "synthetic-private-error" not in caplog.text
