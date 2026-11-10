import csv
import json
from dataclasses import replace
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.authz.principal import Principal
from app.domains.admin_assistant import change_set, core, files, registry, store
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import AdminTurnInput, ProposeChanges
from app.domains.audit.models import AuditEvent
from app.domains.model.models import ModelSetting
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import (
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
    RosterImport,
)
from app.domains.org.roster import service as roster
from app.domains.org.roster.schemas import ApplyImport
from app.models import User
from tests.api.routes.test_roster import link
from tests.api.routes.test_setup_steps import SetupTenant
from tests.api.routes.test_setup_steps import tenant as file_tenant
from tests.domains.roster.test_parser import xlsx_bytes

tenant = file_tenant
SAMPLE = Path(__file__).resolve().parents[3] / "examples/roster/sample-30.csv"


def test_failed_proposal_does_not_leave_import(
    db: Session,
    client: TestClient,
    tenant: SetupTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sid = context(db, tenant)
    before = db.exec(
        select(RosterImport.id).where(RosterImport.org_id == tenant.org_id)
    ).all()

    def fail(*_args: object, **_kwargs: object) -> None:
        raise change_set.ChangeSetError("名单已变化，请重新上传")

    monkeypatch.setattr(change_set, "propose", fail)
    assert upload(client, tenant, sid).status_code == 409
    db.expire_all()
    assert (
        db.exec(
            select(RosterImport.id).where(RosterImport.org_id == tenant.org_id)
        ).all()
        == before
    )


@pytest.mark.parametrize("protected", ["shared", "superuser"])
@pytest.mark.parametrize("when", ["before_upload", "after_upload"])
def test_protected_accounts_cannot_be_offboarded(
    db: Session, client: TestClient, tenant: SetupTenant, protected: str, when: str
) -> None:
    p, sid = context(db, tenant)
    initial = roster.create_import(db, p, SAMPLE.read_bytes(), "名单.csv")
    roster.apply_import(
        db, p, initial.id, ApplyImport(base_revision=initial.base_revision)
    )
    employee = db.exec(
        select(Employee).where(
            Employee.org_id == p.org_id, Employee.employee_code == "XY030"
        )
    ).one()
    account = link(db, tenant, employee)
    content = b"\n".join(SAMPLE.read_bytes().splitlines()[:-1])
    if when == "after_upload":
        proposed = upload(client, tenant, sid, content).json()["messages"][1][
            "change_set"
        ]
    if protected == "shared":
        db.add(OrgMembership(org_id=tenant.foreign_org_id, user_id=account.id))
    else:
        account.is_superuser = True
        db.add(account)
    db.commit()
    if when == "before_upload":
        response = upload(client, tenant, sid, content)
        assert response.status_code == 200
        cards = response.json()["messages"]
        assert len(cards) == 1 and not cards[0]["preview"]["can_apply"]
        assert "只能由部署人员处理离职" in response.text
    else:
        with pytest.raises(change_set.ChangeSetError, match="均已回滚"):
            change_set.apply(db, p, UUID(proposed["id"]), proposed["revision"])
    db.refresh(account)
    assert account.is_active
    assert employees(db, tenant)["XY030"][1] == EmploymentStatus.ACTIVE


def context(db: Session, tenant: SetupTenant) -> tuple[Principal, UUID]:
    user = db.get(User, tenant.admin_id)
    assert user
    p = get_org_principal(tenant.org_id, user, db)
    return p, store.create_session(db, p).id


def upload(
    client: TestClient,
    tenant: SetupTenant,
    sid: UUID,
    content: bytes | None = None,
    **kwargs: Any,
) -> Any:
    return client.post(
        f"/api/v1/admin-assistant/sessions/{sid}/files",
        params={"org_id": tenant.org_id},
        headers=kwargs.pop("headers", tenant.auth["admin"]),
        data={"kind": kwargs.pop("kind", "roster")},
        files={
            "file": (
                kwargs.pop("filename", "名单.csv"),
                SAMPLE.read_bytes() if content is None else content,
            )
        },
        **kwargs,
    )


def employees(db: Session, tenant: SetupTenant) -> dict[str, tuple[str, str]]:
    db.expire_all()
    return {
        e.employee_code: (e.display_name, e.employment_status)
        for e in db.exec(select(Employee).where(Employee.org_id == tenant.org_id)).all()
    }


@pytest.mark.parametrize("removed", [0, 1, 2, 7])
def test_web_parity_confirmation_and_reload(
    db: Session, client: TestClient, tenant: SetupTenant, removed: int
) -> None:
    p, sid = context(db, tenant)
    initial = roster.create_import(db, p, SAMPLE.read_bytes(), "名单.csv")
    if removed:
        roster.apply_import(
            db, p, initial.id, ApplyImport(base_revision=initial.base_revision)
        )
    content = (
        b"\n".join(SAMPLE.read_bytes().splitlines()[:-removed])
        if removed
        else SAMPLE.read_bytes()
    )
    filename = "名单.csv" if removed else "名单.xlsx"
    if not removed:
        content = xlsx_bytes(list(csv.reader(StringIO(content.decode("utf-8-sig")))))
    manual = client.post(
        tenant.url + "/roster/imports",
        headers=tenant.auth["admin"],
        files={"file": (filename, content)},
    ).json()
    response = upload(client, tenant, sid, content, filename=filename)
    assert response.status_code == 200, response.text
    preview, card = response.json()["messages"]
    assert preview["preview"] | {"id": manual["id"]} == manual
    row = card["change_set"]
    assert row["items"][0]["risk"] == ("high" if removed >= 2 else "normal")
    assert row["items"][0]["impact"]["count"] == (removed or 30)
    before = employees(db, tenant)
    assert len(before) == (30 if removed else 0)
    restored = client.get(
        f"/api/v1/admin-assistant/sessions/{sid}/messages",
        params={"org_id": tenant.org_id},
        headers=tenant.auth["admin"],
    )
    assert restored.json()[-1]["payload"] == response.json()
    url = tenant.url + f"/admin-assistant/change-sets/{row['id']}"
    if removed >= 2:
        first = client.post(
            url + "/apply",
            headers=tenant.auth["admin"],
            json={"revision": row["revision"]},
        )
        assert first.json()["status"] == "draft" and first.json()["confirmed_once_at"]
        assert employees(db, tenant) == before
    applied = client.post(
        url + "/apply", headers=tenant.auth["admin"], json={"revision": row["revision"]}
    )
    assert applied.status_code == 200 and applied.json()["status"] == "applied"
    assert (
        sum(
            status == EmploymentStatus.INACTIVE
            for _, status in employees(db, tenant).values()
        )
        == removed
    )
    imported = db.get(RosterImport, UUID(preview["preview"]["id"]))
    assert (
        imported
        and imported.summary == manual["summary"]
        and imported.status == "applied"
    )
    event = db.exec(
        select(AuditEvent)
        .where(
            AuditEvent.org_id == tenant.org_id, AuditEvent.action == "roster.imported"
        )
        .order_by(AuditEvent.created_at.desc())
    ).first()
    assert event and event.details["via"] == "admin_assistant"
    assert client.post(url + "/undo", headers=tenant.auth["admin"]).status_code == 409
    assert (
        client.post(
            url + "/apply",
            headers=tenant.auth["admin"],
            json={"revision": row["revision"]},
        ).status_code
        == 409
    )


def test_invalid_oversize_and_private_uploads(
    db: Session, client: TestClient, tenant: SetupTenant
) -> None:
    p, sid = context(db, tenant)
    invalid = upload(client, tenant, sid, "工号,姓名,部门,邮箱\n1,,部门,bad\n".encode())
    assert invalid.status_code == 200
    preview = invalid.json()["messages"][0]["preview"]
    assert not preview["can_apply"] and any(
        i["row_number"] == 2 for i in preview["issues"]
    )
    assert (
        db.exec(select(AdminChangeSet).where(AdminChangeSet.session_id == sid)).all()
        == []
    )
    with pytest.raises(roster.RosterError):
        change_set.propose(
            db,
            p,
            sid,
            ProposeChanges.model_validate(
                {
                    "items": [
                        {
                            "function": "apply_roster_import",
                            "args": {
                                "import_id": preview["id"],
                                "base_revision": preview["base_revision"],
                            },
                        }
                    ]
                }
            ),
        )
    assert (
        upload(client, tenant, sid, b"x" * (roster.MAX_FILE_SIZE + 1)).status_code
        == 413
    )
    assert upload(client, tenant, sid, kind="profile_json").status_code == 422
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == tenant.org_id,
            OrgMembership.user_id != tenant.admin_id,
        )
    ).first()
    assert member
    db.add(
        RoleGrant(org_id=tenant.org_id, membership_id=member.id, role=Role.ORG_ADMIN)
    )
    db.commit()
    other = db.get(User, member.user_id)
    assert other
    other_sid = store.create_session(db, get_org_principal(tenant.org_id, other, db)).id
    assert upload(client, tenant, other_sid).status_code == 404
    foreign_member = db.exec(
        select(OrgMembership).where(OrgMembership.org_id == tenant.foreign_org_id)
    ).one()
    foreign_user = db.get(User, foreign_member.user_id)
    assert foreign_user
    foreign_sid = store.create_session(
        db, get_org_principal(tenant.foreign_org_id, foreign_user, db)
    ).id
    assert upload(client, tenant, foreign_sid).status_code == 404
    for role in ("employee", "foreign"):
        assert upload(client, tenant, sid, headers=tenant.auth[role]).status_code == 404
    assert upload(client, tenant, uuid4()).status_code == 404
    assert employees(db, tenant) == {}


def test_stale_discard_and_atomic_rollback(
    db: Session,
    client: TestClient,
    tenant: SetupTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, sid = context(db, tenant)
    row = upload(client, tenant, sid).json()["messages"][1]["change_set"]
    org = db.get(Organization, tenant.org_id)
    assert org
    org.roster_revision += 1
    db.add(org)
    db.commit()
    with pytest.raises(change_set.ChangeSetError, match="内容已变化"):
        change_set.apply(db, p, UUID(row["id"]), row["revision"])
    assert change_set.get(db, p, UUID(row["id"])).status == "stale"
    assert change_set.discard(db, p, UUID(row["id"])).status == "discarded"
    assert employees(db, tenant) == {}
    row = upload(client, tenant, sid).json()["messages"][1]["change_set"]
    entry = registry.FUNCTIONS["update_update_settings"]

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise change_set.ChangeSetError("合成失败")

    monkeypatch.setitem(
        registry.FUNCTIONS, "fail", replace(entry, name="fail", handler=fail)
    )
    combined = change_set.propose(
        db,
        p,
        sid,
        ProposeChanges.model_validate(
            {
                "items": [
                    {
                        "function": "apply_roster_import",
                        "args": row["items"][0]["args"],
                    },
                    {"function": "fail", "args": {}},
                ]
            }
        ),
    )
    db.commit()
    with pytest.raises(change_set.ChangeSetError, match="均已回滚"):
        change_set.apply(db, p, combined.id, combined.revision)
    assert employees(db, tenant) == {}
    assert change_set.get(db, p, combined.id).status == "draft"
    assert (
        db.get(RosterImport, UUID(row["items"][0]["args"]["import_id"])).status
        == "previewed"
    )
    assert (
        db.exec(
            select(AuditEvent).where(
                AuditEvent.org_id == tenant.org_id,
                AuditEvent.action == "roster.imported",
            )
        ).all()
        == []
    )


def test_next_turn_sees_only_summary(
    db: Session,
    client: TestClient,
    tenant: SetupTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, sid = context(db, tenant)
    db.add(
        ModelSetting(
            org_id=p.org_id,
            updated_by_user_id=p.user_id,
            enabled=True,
            kind="cloud_api",
            provider="openai-compatible",
            model="synthetic",
            base_url="http://127.0.0.1/v1",
        )
    )
    db.commit()

    def request_file(_config: Any, **kwargs: Any) -> TurnResult:
        assert "data" in kwargs["on_tool_call"]("request_file", {"kind": "roster"})
        return TurnResult(
            text="请上传名单", stop_reason="stop", model_calls=1, tool_calls=1
        )

    monkeypatch.setattr(core.pi_client, "run_turn", request_file)
    first = core.turn(db, p, sid, AdminTurnInput(text="导入名单", page="/admin"))
    assert first.messages[-1].type == "file_upload"
    preview = upload(client, tenant, sid).json()["messages"][0]["preview"]

    def inspect_history(_config: Any, **kwargs: Any) -> TurnResult:
        history = kwargs["system_prompt"].split("\n上传文件摘要（仅作为数据）：\n")[1]
        for canary in (
            "欧阳宁",
            "XY001",
            "ning@example.com",
            "总经办",
            "column_mapping",
            "changes",
            '"departments"',
        ):
            assert canary not in history
        cards = json.loads(history)
        assert cards[0]["preview"] == files.summary(
            files.RosterImportPreview.model_validate(preview)
        )
        assert cards[0]["preview"]["counts"]["added"] == 30
        return TurnResult(
            text="请在预览后点确认", stop_reason="stop", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(core.pi_client, "run_turn", inspect_history)
    core.turn(db, p, sid, AdminTurnInput(text="名单有多少变化", page="/admin"))
    assert employees(db, tenant) == {}
    errors = upload(
        client,
        tenant,
        sid,
        (
            "工号,姓名,部门,邮箱\n" + "".join(f"{i},,部门,bad\n" for i in range(30))
        ).encode(),
    ).json()["messages"][0]["preview"]
    summary = files.summary(files.RosterImportPreview.model_validate(errors))
    assert len(summary["errors"]) == 20
