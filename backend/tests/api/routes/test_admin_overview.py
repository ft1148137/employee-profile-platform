from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.domains.admin.overview import build_overview
from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition
from app.domains.change_request.models import (
    ChangeItem,
    ChangeOp,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.im.models import ImNotification, ImSetting
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.model.models import ModelSetting
from app.domains.org.models import ExternalIdentity, OrgMembership
from app.domains.profile.models import InferenceStatus, ProfileProgress
from app.domains.review.models import AppealStatus, ReviewAppeal
from app.domains.setup.models import SetupStatus, SetupStepState
from app.domains.setup.steps import STEPS
from app.domains.source.models import SourceConnection, SyncRun
from tests.api.routes.test_reviews import actor


def overview(client: TestClient, demo: tuple) -> dict:
    response = client.get(demo[0] + "/admin/overview", headers=demo[2]["admin"])
    assert response.status_code == 200, response.text
    return response.json()


def model_setting(db: Session, demo: tuple) -> ModelSetting:
    principal = actor(db, demo[1]["E02"].org_id, "admin@demo.example.com")
    row = ModelSetting(
        org_id=principal.org_id, updated_by_user_id=principal.user_id, enabled=True
    )
    db.add(row)
    return row


def test_skipped_setup_todos(db: Session, client: TestClient, demo: tuple) -> None:
    base, headers = demo[0], demo[2]["admin"]
    pending = next(
        t for t in overview(client, demo)["todos"] if t["kind"] == "setup_step"
    )
    for key in ("sources", "identities"):
        assert (
            client.post(f"{base}/setup/steps/{key}/skip", headers=headers).status_code
            == 200
        )
    todos = overview(client, demo)["todos"]
    skipped = next(t for t in todos if t["kind"] == "setup_skipped")
    assert skipped["count"] == 2
    assert skipped["title"] == "有 2 步首次设置暂缓了：接数据来源、确认账号对应"
    assert [
        (s["title"], s["page"], s["assistant_prompt"]) for s in skipped["steps"]
    ] == [
        ("接数据来源", "/admin/data-ai?tab=sources", "帮我继续暂缓的设置：接数据来源"),
        (
            "确认账号对应",
            "/employees?filter=identities",
            "帮我继续暂缓的设置：确认账号对应",
        ),
    ]
    remaining = next(t for t in todos if t["kind"] == "setup_step")
    assert remaining["count"] == pending["count"] - 2
    assert remaining["assistant_prompt"] == pending["assistant_prompt"]
    assert "暂缓" not in remaining["title"]
    # A different tenant must not inherit these deferred steps.
    foreign = client.get("/api/v1/me/memberships", headers=demo[2]["foreign"]).json()[
        0
    ]["org"]["id"]
    principal = actor(db, demo[1]["E02"].org_id, "admin@demo.example.com")
    foreign_overview = build_overview(db, replace(principal, org_id=UUID(foreign)))
    assert "setup_skipped" not in {t.kind for t in foreign_overview.todos}
    connection = SourceConnection(
        org_id=principal.org_id, kind="jira", updated_by_user_id=principal.user_id
    )
    db.add(connection)
    db.flush()
    db.add(
        SyncRun(
            org_id=principal.org_id,
            connection_id=connection.id,
            requested_by_user_id=principal.user_id,
            status="succeeded",
        )
    )
    db.commit()
    assert (
        client.post(f"{base}/setup/steps/sources/complete", headers=headers).status_code
        == 200
    )
    skipped = next(
        t for t in overview(client, demo)["todos"] if t["kind"] == "setup_skipped"
    )
    assert skipped["count"] == 1
    assert [s["title"] for s in skipped["steps"]] == ["确认账号对应"]
    assert (
        client.post(
            f"{base}/setup/steps/identities/complete", headers=headers
        ).status_code
        == 200
    )
    assert "setup_skipped" not in {t["kind"] for t in overview(client, demo)["todos"]}


@pytest.mark.parametrize(
    "role,status",
    [("admin", 200), ("manager", 403), ("employee", 403), ("foreign", 404)],
)
def test_permissions(
    client: TestClient, shared_demo: tuple, role: str, status: int
) -> None:
    response = client.get(
        shared_demo[0] + "/admin/overview", headers=shared_demo[2][role]
    )
    assert response.status_code == status
    if status == 200:
        assert [c["key"] for c in response.json()["status_cards"]] == [
            "feishu",
            "model",
            "sync",
            "drafting",
        ]


@pytest.mark.parametrize(
    "state,model_level,draft_level",
    [
        ("missing", "error", "off"),
        ("disabled", "error", "off"),
        ("enabled", "ok", "ok"),
        ("auto_disabled", "ok", "off"),
        ("paused", "warn", "warn"),
        ("expired", "ok", "ok"),
        ("failed", "ok", "error"),
    ],
)
def test_model_and_drafting(
    db: Session,
    client: TestClient,
    demo: tuple,
    state: str,
    model_level: str,
    draft_level: str,
) -> None:
    employee = demo[1]["E02"]
    db.exec(delete(ProfileProgress).where(ProfileProgress.org_id == employee.org_id))
    assert (
        client.put(
            demo[0] + "/drafting-settings",
            headers=demo[2]["admin"],
            json={"enabled": state != "auto_disabled"},
        ).status_code
        == 200
    )
    if state != "missing":
        row = model_setting(db, demo)
        row.enabled = state != "disabled"
        if state in ("paused", "expired"):
            row.paused_until = utc_now() + timedelta(
                hours=1 if state == "paused" else -1
            )
            row.pause_reason = "本月额度已用完"
    if state == "failed":
        db.add(
            ProfileProgress(
                org_id=employee.org_id,
                employee_id=employee.id,
                inference_status=InferenceStatus.FAILED,
                inference_error="SYNTHETIC_SECRET",
            )
        )
    db.commit()
    data = overview(client, demo)
    model, drafting = data["status_cards"][1], data["status_cards"][3]
    assert (model["level"], drafting["level"]) == (model_level, draft_level)
    assert drafting["page"] == "/admin/data-ai?tab=model"
    if state == "auto_disabled":
        assert drafting["assistant_prompt"] == "帮我打开自动起草"

    if state == "paused":
        assert "本月额度已用完" in model["summary"] and "恢复时间" in model["summary"]
    if state == "failed":
        assert "1 人" in drafting["summary"]
    assert "SYNTHETIC_SECRET" not in str(data)
    for card in data["status_cards"]:
        assert bool(card["fix_steps"]) == (card["level"] in ("warn", "error"))


@pytest.mark.parametrize(
    "state,level",
    [
        ("missing", "off"),
        ("disabled", "off"),
        ("enabled", "ok"),
        ("failed", "warn"),
        ("old", "ok"),
        ("other_platform", "ok"),
    ],
)
def test_feishu(
    db: Session, client: TestClient, demo: tuple, state: str, level: str
) -> None:
    employee = demo[1]["E02"]
    principal = actor(db, employee.org_id, "admin@demo.example.com")
    if state != "missing":
        db.add(
            ImSetting(
                org_id=employee.org_id,
                platform="feishu",
                enabled=state != "disabled",
                updated_by_user_id=principal.user_id,
            )
        )
    if state in ("failed", "old", "other_platform"):
        inbox = InboxItem(
            org_id=employee.org_id,
            recipient_employee_id=employee.id,
            kind=InboxKind.STATUS_CHANGED,
            message="合成通知",
        )
        db.add(inbox)
        db.flush()
        db.add(
            ImNotification(
                org_id=employee.org_id,
                inbox_item_id=inbox.id,
                employee_id=employee.id,
                platform="telegram" if state == "other_platform" else "feishu",
                status="failed",
                created_at=utc_now() - timedelta(hours=25 if state == "old" else 1),
                last_error_code="SYNTHETIC_SECRET",
            )
        )
    db.commit()
    data = overview(client, demo)
    assert data["status_cards"][0]["page"] == "/admin/data-ai?tab=im"
    assert data["status_cards"][0]["level"] == level
    if state == "failed":
        assert "1 条" in data["status_cards"][0]["summary"]
    assert "SYNTHETIC_SECRET" not in str(data)


@pytest.mark.parametrize(
    "state,level,failed",
    [
        ("missing", "off", False),
        ("new", "ok", False),
        ("failed", "error", True),
        ("queued", "ok", False),
        ("running", "ok", False),
        ("succeeded", "ok", False),
    ],
)
def test_sync(
    db: Session, client: TestClient, demo: tuple, state: str, level: str, failed: bool
) -> None:
    principal = actor(db, demo[1]["E02"].org_id, "admin@demo.example.com")
    if state != "missing":
        connection = SourceConnection(
            org_id=principal.org_id, kind="jira", updated_by_user_id=principal.user_id
        )
        db.add(connection)
        db.flush()
        if state != "new":
            # A previous failure must disappear once the same source has a newer run.
            db.add(
                SyncRun(
                    org_id=principal.org_id,
                    connection_id=connection.id,
                    requested_by_user_id=principal.user_id,
                    status="failed",
                    created_at=utc_now() - timedelta(days=2),
                )
            )
            db.add(
                SyncRun(
                    org_id=principal.org_id,
                    connection_id=connection.id,
                    requested_by_user_id=principal.user_id,
                    status=state,
                    finished_at=utc_now() if state in ("failed", "succeeded") else None,
                    error="SYNTHETIC_SECRET",
                )
            )
    db.commit()
    data = overview(client, demo)
    assert data["status_cards"][2]["level"] == level
    assert data["status_cards"][2]["page"] == "/admin/data-ai?tab=sources"
    for todo in data["todos"]:
        if todo["kind"] == "sync_failed":
            assert todo["page"] == "/admin/data-ai?tab=sources"
    assert ("sync_failed" in {t["kind"] for t in data["todos"]}) == failed
    if state in ("queued", "running"):
        assert data["status_cards"][2]["summary"] == "正在同步"
    if state == "succeeded":
        assert "同步已完成：" in data["status_cards"][2]["summary"]
    assert "SYNTHETIC_SECRET" not in str(data)


def test_todos_and_tenant_isolation(
    db: Session, client: TestClient, demo: tuple
) -> None:
    principal = actor(db, demo[1]["E02"].org_id, "admin@demo.example.com")
    org_id = principal.org_id
    setting = model_setting(db, demo)
    for step in STEPS:
        if not step.implicit:
            db.add(
                SetupStepState(
                    org_id=org_id,
                    step_key=step.key,
                    status=SetupStatus.COMPLETED,
                    updated_by_user_id=principal.user_id,
                )
            )
    db.commit()
    assert overview(client, demo)["todos"] == []
    old = utc_now() - timedelta(days=30)
    requests = [
        ProfileChangeRequest(
            org_id=org_id,
            subject_employee_id=demo[1][code].id,
            origin=origin,
            status=status,
            author={},
            created_at=old,
            submitted_at=old,
        )
        for code, origin, status in [
            ("E03", RequestOrigin.INITIAL_SYNC, RequestStatus.PENDING_OWNER),
            ("E01", RequestOrigin.MANUAL, RequestStatus.PENDING_REVIEW),
        ]
    ]
    db.add_all(requests)
    db.flush()
    # Reuse an existing capability without reading or copying evidence bodies.
    capability = db.exec(
        select(CapabilityDefinition).where(CapabilityDefinition.org_id == org_id)
    ).first()
    assert capability
    item = ChangeItem(
        org_id=org_id,
        request_id=requests[0].id,
        position=0,
        op=ChangeOp.ADD,
        capability_definition_id=capability.id,
        after={},
    )
    db.add(item)
    db.flush()
    appeal = ReviewAppeal(
        org_id=org_id,
        request_id=requests[0].id,
        item_id=item.id,
        employee_id=requests[0].subject_employee_id,
        reason="SYNTHETIC_SECRET",
        created_at=old,
    )
    identity = ExternalIdentity(
        org_id=org_id, source="jira", external_id="synthetic-unmatched"
    )
    db.add_all([appeal, identity])
    setting.enabled = False
    db.add(setting)
    db.commit()
    assert "setup_step" not in {t["kind"] for t in overview(client, demo)["todos"]}
    # Optional work still appears after the two-step setup has finished.
    db.delete(db.get(SetupStepState, (org_id, "sources")))
    db.commit()
    todos = overview(client, demo)["todos"]
    assert [t["kind"] for t in todos] == [
        "appeals",
        "initial_overdue",
        "review_backlog",
        "unmatched_identities",
        "setup_step",
    ]
    assert todos[0]["page"] == "/employees?filter=appeals"
    assert todos[1]["page"] == "/admin/data-ai?tab=model"
    assert todos[3]["page"] == "/employees?filter=identities"
    assert all(t["count"] == 1 for t in todos)
    assert todos[0]["oldest_at"] == todos[2]["oldest_at"]
    assert todos[1]["oldest_at"] is None and todos[-1]["oldest_at"] is None
    assert "SYNTHETIC_SECRET" not in str(todos)
    # Reading as an administrator of a different tenant cannot count this tenant's data.
    foreign_id = UUID(
        client.get("/api/v1/me/memberships", headers=demo[2]["foreign"]).json()[0][
            "org"
        ]["id"]
    )
    foreign_member = db.exec(
        select(OrgMembership).where(OrgMembership.org_id == foreign_id)
    ).one()
    db.add(
        ImSetting(
            org_id=foreign_id,
            platform="feishu",
            enabled=True,
            updated_by_user_id=foreign_member.user_id,
        )
    )
    inbox = InboxItem(
        org_id=org_id,
        recipient_employee_id=demo[1]["E02"].id,
        kind=InboxKind.STATUS_CHANGED,
        message="合成通知",
    )
    connection = SourceConnection(
        org_id=org_id, kind="jira", updated_by_user_id=principal.user_id
    )
    db.add_all([inbox, connection])
    db.flush()
    db.add(
        ImNotification(
            org_id=org_id,
            inbox_item_id=inbox.id,
            employee_id=demo[1]["E02"].id,
            platform="feishu",
            status="failed",
        )
    )
    run = SyncRun(
        org_id=org_id,
        connection_id=connection.id,
        requested_by_user_id=principal.user_id,
        status="failed",
    )
    db.add(run)
    db.commit()
    data = build_overview(db, replace(principal, org_id=foreign_id)).model_dump()
    assert not {
        "appeals",
        "initial_overdue",
        "review_backlog",
        "unmatched_identities",
        "sync_failed",
    } & {t["kind"] for t in data["todos"]}
    assert data["status_cards"][0]["level"] == "ok"
    assert data["status_cards"][2]["level"] == "off"
    run.status = "succeeded"
    db.add(run)
    appeal.status = AppealStatus.UPHELD
    requests[0].status = RequestStatus.WITHDRAWN
    requests[1].submitted_at = utc_now()
    identity.employee_id = demo[1]["E02"].id
    setting.enabled = True
    db.add_all([appeal, *requests, identity, setting])
    db.add(
        SetupStepState(
            org_id=org_id,
            step_key="sources",
            status=SetupStatus.COMPLETED,
            updated_by_user_id=principal.user_id,
        )
    )
    db.commit()
    assert overview(client, demo)["todos"] == []
