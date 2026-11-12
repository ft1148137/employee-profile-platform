from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, select

from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import core, files, registry, store
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import (
    AdminTurnInput,
    AdminTurnResponse,
    ProposeChanges,
)
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.bot import attachments
from app.domains.model.models import ModelSetting
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import (
    Employee,
    EmploymentStatus,
    ExternalIdentity,
    Organization,
    OrgMembership,
    ReportingLine,
    RosterImport,
)
from app.domains.org.roster import service, single
from tests.admin_assistant.test_personnel import principal
from tests.admin_assistant.test_personnel import roster as roster
from tests.admin_assistant.test_personnel import tenant as tenant
from tests.api.routes.test_roster import client as client
from tests.api.routes.test_roster import employees, link
from tests.api.routes.test_setup_steps import SetupTenant

pytestmark = pytest.mark.usefixtures("roster")


def args(rows: list[dict]) -> dict:
    return {"materials": [{"label": "管理员描述", "attachment_id": None}], "rows": rows}


def propose(
    db: Session, tenant: SetupTenant, values: dict, sid: UUID | None = None
) -> AdminChangeSet:
    p = principal(db, tenant)
    row = c.propose(
        db,
        p,
        sid or store.create_session(db, p).id,
        ProposeChanges.model_validate(
            {"items": [{"function": "draft_roster_from_material", "args": values}]}
        ),
    )
    db.commit()
    return row


def state(db: Session, tenant: SetupTenant) -> list:
    db.expire_all()
    return [
        [
            r.model_dump(mode="json")
            for r in db.exec(
                select(model).where(model.org_id == tenant.org_id).order_by(model.id)
            ).all()
        ]
        for model in (Employee, ReportingLine, RosterImport, AuditEvent)
    ]


@pytest.mark.parametrize("source", ["xlsx", "description"])
def test_model_material_confirmation(
    db: Session,
    tenant: SetupTenant,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    p = principal(db, tenant)
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
    sid = store.create_session(db, p).id
    values = args([{"action": "update", "employee_code": "B", "department": "平台组"}])
    ids = []
    if source == "xlsx":
        values["rows"] = [
            {
                "action": "onboard",
                "employee_code": code,
                "display_name": code,
                "department": "研发/平台组",
                "email": f"{uuid4()}@example.com",
                "manager_code": "A",
            }
            for code in ("D", "E", "F")
        ]
        book = BytesIO()
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active.append(["叫啥", "所在团队", "做什么", "联系邮箱"])
        for row in values["rows"]:
            workbook.active.append(
                [row["display_name"], row["department"], "工程师", row["email"]]
            )
        workbook.save(book)
        workbook.close()
        attachment = attachments.upload(
            db,
            p,
            "admin",
            sid,
            UploadFile(filename="人员材料.xlsx", file=BytesIO(book.getvalue())),
        )
        ids = [attachment.id]
        values["materials"] = [
            {"label": "人员材料.xlsx", "attachment_id": str(attachment.id)}
        ]
        identity = ExternalIdentity(
            org_id=p.org_id,
            source="jira",
            external_id=str(uuid4()),
            email=values["rows"][0]["email"],
        )
        db.add(identity)
        db.commit()
    if source == "description":
        org = db.get_one(Organization, p.org_id)
        org.employee_fields = ["work_location"]
        db.add(org)
        db.commit()
        values["rows"][0]["work_location"] = "上海"
        values["rows"][0]["employee_code"] = " B "
    before = state(db, tenant)

    def reply(_config: object, **kwargs: Any) -> TurnResult:
        assert "draft_roster_from_material" in kwargs["system_prompt"]
        if ids:
            assert "叫啥" in kwargs["input"] and str(ids[0]) in kwargs["input"]
        result = kwargs["on_tool_call"](
            "propose_changes",
            {"items": [{"function": "draft_roster_from_material", "args": values}]},
        )
        assert "error" not in result, result
        return TurnResult(
            text="请核对人员变动", stop_reason="stop", model_calls=1, tool_calls=1
        )

    monkeypatch.setattr(core.pi_client, "run_turn", reply)
    response = core.turn(
        db,
        p,
        sid,
        AdminTurnInput(
            text="乙从研发调到平台组", page="/employees", attachment_ids=ids
        ),
    )
    card = response.messages[1].change_set
    assert state(db, tenant) == before
    assert card.items[0].impact.count == (3 if ids else 1)
    assert ("新增 3 人" if ids else "调整 1 人") in card.items[0].impact.text
    assert (
        card.items[0].risk == ("high" if ids else "normal")
        and not card.items[0].undoable
    )
    url = f"{tenant.url}/admin-assistant/change-sets/{card.id}/apply"
    for role in ("manager", "employee", "foreign"):
        assert client.post(
            url, headers=tenant.auth[role], json={"revision": card.revision}
        ).status_code in (403, 404)
    if ids:
        assert c.apply(db, p, card.id, card.revision).status == "draft"
        assert state(db, tenant) == before
    assert c.apply(db, p, card.id, card.revision).status == "applied"
    people = employees(db, tenant)
    assert all(
        people[code].employment_status == EmploymentStatus.ACTIVE
        for code in ("A", "B", "C")
    )
    if ids:
        db.refresh(identity)
        assert identity.employee_id == people["D"].id
        assert (
            db.exec(
                select(ReportingLine).where(
                    ReportingLine.org_id == p.org_id,
                    ReportingLine.employee_id == people["D"].id,
                )
            )
            .one()
            .manager_employee_id
            == people["A"].id
        )
    else:
        assert people["B"].title == "工程师" and people["B"].work_location == "上海"
        assert single.current_roster(db, p).rows[1].department_path == ("平台组",)
    event = db.exec(
        select(AuditEvent)
        .where(AuditEvent.org_id == p.org_id, AuditEvent.action == "roster.imported")
        .order_by(AuditEvent.created_at.desc())
    ).first()
    assert event.details["material_names"] == [values["materials"][0]["label"]]
    assert event.details["assistant_session_id"] == str(sid)
    record = db.get_one(RosterImport, UUID(event.target_id))
    assert record.filename == values["materials"][0]["label"]


@pytest.mark.parametrize(
    "rows,problem",
    [
        (
            [{"action": "update", "employee_code": "B", "manager_code": "missing"}],
            "名单里没有工号 missing",
        ),
        ([{"action": "update", "employee_code": "B"}] * 2, "工号重复"),
        ([{"action": "update", "employee_code": "B", "department": ""}], "参数说明"),
        ([{"action": "update", "employee_code": "B", "job_family": "技术"}], "未开启"),
        (
            [{"action": "update", "employee_code": "B", "email": "bad"}],
            "邮箱格式不正确",
        ),
    ],
)
def test_invalid_roster_never_applies(
    db: Session, tenant: SetupTenant, rows: list[dict], problem: str
) -> None:
    before = state(db, tenant)
    try:
        row = propose(db, tenant, args(rows))
    except HTTPException as error:
        assert problem in str(error.detail)
    else:
        assert problem in row.items[0]["impact"]["text"]
        with pytest.raises(c.ChangeSetError, match="均已回滚"):
            c.apply(db, principal(db, tenant), row.id, row.revision)
    assert state(db, tenant) == before


@pytest.mark.parametrize("protected", ["shared", "superuser"])
@pytest.mark.parametrize("when", ["before", "after"])
def test_protected_offboarding(
    db: Session, tenant: SetupTenant, roster: dict, protected: str, when: str
) -> None:
    p = principal(db, tenant)
    account = link(db, tenant, roster["B"])
    values = args([{"action": "offboard", "employee_code": "B"}])
    row = propose(db, tenant, values) if when == "after" else None
    if protected == "shared":
        db.add(OrgMembership(org_id=tenant.foreign_org_id, user_id=account.id))
    else:
        account.is_superuser = True
        db.add(account)
    db.commit()
    row = row or propose(db, tenant, values)
    assert row.items[0]["risk"] == "high"
    if when == "before":
        assert "只能由部署人员处理离职" in row.items[0]["impact"]["text"]
    before = state(db, tenant)
    assert c.apply(db, p, row.id, row.revision).status == "draft"
    with pytest.raises(c.ChangeSetError, match="均已回滚"):
        c.apply(db, p, row.id, row.revision)
    assert state(db, tenant) == before
    db.refresh(account)
    assert account.is_active


def test_offboard_stale_and_rollback(
    db: Session, tenant: SetupTenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = principal(db, tenant)
    values = args(
        [
            {"action": "offboard", "employee_code": "B"},
            {"action": "update", "employee_code": "C", "title": "新职位"},
        ]
    )
    row = propose(db, tenant, values)
    org = db.get_one(Organization, p.org_id)
    org.roster_revision += 1
    db.add(org)
    db.commit()
    with pytest.raises(c.ChangeSetError, match="已变化"):
        c.apply(db, p, row.id, row.revision)
    row = propose(db, tenant, values)
    assert c.apply(db, p, row.id, row.revision).status == "draft"
    before = state(db, tenant)
    original = service.auto_link_identities

    def fail(*_args: object, **_kwargs: object) -> None:
        raise service.RosterError(422, "合成中途失败")

    monkeypatch.setattr(service, "auto_link_identities", fail)
    with pytest.raises(c.ChangeSetError, match="均已回滚"):
        c.apply(db, p, row.id, row.revision)
    assert state(db, tenant) == before
    assert c.get(db, p, row.id).status == "draft"
    monkeypatch.setattr(service, "auto_link_identities", original)
    assert c.apply(db, p, row.id, row.revision).status == "applied"
    assert employees(db, tenant)["B"].employment_status == EmploymentStatus.INACTIVE
    assert employees(db, tenant)["C"].title == "新职位"


@pytest.mark.parametrize("failure", ["session", "expired", "tenant", "nonadmin"])
def test_attachment_and_identity_boundaries(
    db: Session, tenant: SetupTenant, failure: str
) -> None:
    p = principal(db, tenant)
    sid = store.create_session(db, p).id
    attachment = attachments.upload(
        db,
        p,
        "admin",
        sid,
        UploadFile(filename="人员.txt", file=BytesIO("合成材料".encode())),
    )
    values = args([{"action": "update", "employee_code": "B", "title": "新职位"}])
    values["materials"] = [{"label": "人员.txt", "attachment_id": str(attachment.id)}]
    if failure == "session":
        sid = store.create_session(db, p).id
    elif failure == "expired":
        draft = propose(db, tenant, values, sid)
        record = attachments.get_session_attachment(db, p, "admin", sid, attachment.id)
        record.created_at = utc_now() - timedelta(days=366)
        db.add(record)
        db.commit()
    elif failure == "tenant":
        values["rows"][0]["employee_code"] = "FOREIGN"
    else:
        p = replace(p, is_org_admin=False)
    before = state(db, tenant)
    if failure == "expired":
        with pytest.raises(c.ChangeSetError, match="附件不属于当前会话或已过期"):
            c.apply(db, p, draft.id, draft.revision)
    with pytest.raises((HTTPException, c.InvalidImport)):
        c.propose(
            db,
            p,
            sid,
            ProposeChanges.model_validate(
                {"items": [{"function": "draft_roster_from_material", "args": values}]}
            ),
        )
    assert state(db, tenant) == before


def test_retired_uploads_and_bounded_history(
    db: Session, tenant: SetupTenant, client: TestClient
) -> None:
    p = principal(db, tenant)
    sid = store.create_session(db, p).id
    assert client.get(f"{tenant.url}/roster/template").status_code in (404, 405)
    with pytest.raises(ValidationError):
        registry.FUNCTIONS["draft_roster_from_material"].input_model.model_validate(
            args([{"action": "offboard", "employee_code": "B", "title": "no"}])
        )
    preview = service.create_import(
        db,
        p,
        (
            "工号,姓名,部门,邮箱,上级工号\n"
            + "".join(
                f"X{i},合成{i},研发,x{i}@example.com,{'Y' * 100000}\n"
                for i in range(20)
            )
        ).encode(),
        "旧名单.csv",
    )
    legacy = {
        "type": "file_preview",
        "kind": "roster",
        "preview": preview.model_dump(mode="json"),
    }
    assert AdminTurnResponse(messages=[legacy]).messages[0].kind == "roster"
    summary = files.summary(preview)
    import json

    assert len(json.dumps(summary, ensure_ascii=False)) <= 8000
    assert all(len(e["message"]) <= 200 for e in summary["errors"])
    row = AdminChangeSet(
        org_id=p.org_id,
        session_id=sid,
        created_by_user_id=p.user_id,
        items=[
            {
                "function": "apply_roster_import",
                "args": {},
                "before": None,
                "after": {},
                "risk": "normal",
                "undoable": False,
            }
        ],
    )
    db.add(row)
    db.commit()
    response = client.post(
        f"{tenant.url}/admin-assistant/change-sets/{row.id}/apply",
        json={"revision": row.revision},
    )
    assert response.status_code == 422 and "这个操作已下线" in response.text
    from app.domains.admin_assistant.schemas import RequestFile

    with pytest.raises(ValidationError):
        RequestFile(kind="roster")
