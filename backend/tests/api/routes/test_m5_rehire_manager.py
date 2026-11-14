from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.domains.admin_assistant import change_set, roster_materials, store
from app.domains.admin_assistant.schemas import ProposeChanges
from app.domains.audit.models import AuditEvent
from app.domains.org.models import Employee, EmploymentStatus, ReportingLine
from app.domains.org.roster import single
from tests.admin_assistant.test_personnel import principal
from tests.api.routes.test_roster import apply, employees, upload
from tests.api.routes.test_roster import client as client
from tests.api.routes.test_setup_steps import SetupTenant
from tests.api.routes.test_setup_steps import tenant as tenant


@pytest.fixture
def people(client: TestClient, tenant: SetupTenant, db: Session) -> dict[str, Employee]:
    content = (
        "工号,姓名,部门,上级工号,邮箱\n"
        "A,原上级,研发,,a@example.com\n"
        "B,返聘甲,研发,A,b@example.com\n"
        "C,返聘乙,研发,A,c@example.com\n"
    ).encode()
    assert apply(client, tenant, upload(client, tenant, content)).status_code == 200
    return employees(db, tenant)


def rehire_args(*codes: str, **extra: Any) -> dict[str, Any]:
    return {
        "materials": [{"label": "合成返聘名单", "attachment_id": None}],
        "rows": [
            {
                "action": "onboard",
                "employee_code": code,
                "display_name": "返聘甲" if code == "B" else "返聘乙",
                "department": "研发",
                "email": f"{code.lower()}@example.com",
                **extra,
            }
            for code in codes
        ],
    }


def test_rehire_confirmation_and_result_remind_manager(
    db: Session, tenant: SetupTenant, people: dict[str, Employee]
) -> None:
    p = principal(db, tenant)
    single.apply(db, p, single.Offboard(employee_ids=[people["B"].id]))
    session = store.create_session(db, p)
    args = rehire_args("B")
    proposed = change_set.propose(
        db,
        p,
        session.id,
        ProposeChanges.model_validate(
            {"items": [{"function": "draft_roster_from_material", "args": args}]}
        ),
    )
    text = proposed.items[0]["impact"]["text"]
    assert "返聘甲：直属上级未设置" in text
    assert "原上级 原上级 仍在职，可让我恢复原上级" in text
    db.info["audit_context"] = {"assistant_session_id": str(session.id)}
    result = roster_materials.apply(
        db, p, roster_materials.MaterialArgs.model_validate(args)
    ).model_dump(mode="json")
    assert any("直属上级未设置" in issue["message"] for issue in result["issues"])


@pytest.mark.parametrize(
    "mode", ["active", "inactive", "cycle", "supplied", "multiple", "no_history"]
)
def test_rehire_manager_suggestions(
    db: Session,
    tenant: SetupTenant,
    client: TestClient,
    people: dict[str, Employee],
    mode: str,
) -> None:
    p = principal(db, tenant)
    codes = ["B", "C"] if mode == "multiple" else ["B"]
    single.apply(db, p, single.Offboard(employee_ids=[people[c].id for c in codes]))
    if mode == "inactive":
        single.apply(db, p, single.Offboard(employee_ids=[people["A"].id]))
    elif mode == "no_history":
        codes = ["A"]
        single.apply(db, p, single.Offboard(employee_ids=[people["A"].id]))
    args = roster_materials.MaterialArgs.model_validate(
        rehire_args(*codes, **({"manager_code": "A"} if mode == "supplied" else {}))
    )
    if mode == "cycle":
        # The proposed roster would make A report to B, so B cannot report to A.
        args.rows.append(
            roster_materials.Update(
                action="update", employee_code="A", manager_code="B"
            )
        )
    text = roster_materials.impact(db, p, args).model_dump()["text"]
    if mode == "supplied":
        assert "直属上级未设置" not in text
    else:
        assert "直属上级未设置" in text
        if mode == "inactive":
            assert "原上级 原上级 已离职，请重新设置" in text
        elif mode not in ("cycle", "no_history"):
            assert "仍在职，可让我恢复原上级" in text
        if mode == "multiple":
            assert "返聘甲：直属上级未设置" in text
            assert "返聘乙：直属上级未设置" in text
    if mode in ("inactive", "cycle", "no_history"):
        assert "可让我恢复原上级" not in text
    session = store.create_session(db, p)
    db.info["audit_context"] = {"assistant_session_id": str(session.id)}
    result = roster_materials.apply(db, p, args).model_dump()
    notices = [i for i in result["issues"] if "直属上级未设置" in i["message"]]
    assert len(notices) == (0 if mode == "supplied" else len(codes))
    previous_url = (
        f"{tenant.url}/roster/employees/{people[codes[0]].id}/previous-manager"
    )
    previous = client.get(previous_url)
    assert previous.status_code == 200
    if mode == "no_history":
        assert previous.json() is None
    else:
        assert previous.json() == {
            "id": str(people["A"].id),
            "display_name": "原上级",
            "is_active": mode != "inactive",
            "can_restore": mode not in ("inactive", "cycle"),
        }


def test_restore_uses_transfer_and_existing_audit(
    db: Session, tenant: SetupTenant, client: TestClient, people: dict[str, Employee]
) -> None:
    p = principal(db, tenant)
    single.apply(db, p, single.Offboard(employee_ids=[people["B"].id]))
    args = roster_materials.MaterialArgs.model_validate(rehire_args("B"))
    proposed = change_set.propose(
        db,
        p,
        store.create_session(db, p).id,
        ProposeChanges.model_validate(
            {
                "items": [
                    {
                        "function": "draft_roster_from_material",
                        "args": args.model_dump(mode="json", exclude_unset=True),
                    }
                ]
            }
        ),
    )
    applied = change_set.apply(db, p, proposed.id, proposed.revision)
    assert applied.status == "applied"
    assert applied.items[0].impact is not None
    assert "直属上级未设置" in applied.items[0].impact.text
    previous = client.get(
        f"{tenant.url}/roster/employees/{people['B'].id}/previous-manager"
    ).json()
    for manager_id in (previous["id"], str(people["C"].id), previous["id"]):
        preview = client.post(
            f"{tenant.url}/roster/single-change",
            json={
                "kind": "transfer",
                "employee_id": str(people["B"].id),
                "manager_id": manager_id,
            },
        )
        assert preview.status_code == 200
        assert apply(client, tenant, preview.json()).status_code == 200
        db.expire_all()
        line = db.exec(
            select(ReportingLine).where(
                ReportingLine.org_id == tenant.org_id,
                ReportingLine.employee_id == people["B"].id,
                col(ReportingLine.valid_to).is_(None),
            )
        ).one()
        assert str(line.manager_employee_id) == manager_id
        event = db.exec(
            select(AuditEvent)
            .where(
                AuditEvent.org_id == tenant.org_id,
                AuditEvent.action == "roster.imported",
            )
            .order_by(col(AuditEvent.created_at).desc())
        ).first()
        assert event is not None
        assert event.details["manager_changed"] == 1
    # The most recently ended line wins after another ordinary manager change.
    assert client.get(
        f"{tenant.url}/roster/employees/{people['B'].id}/previous-manager"
    ).json()["id"] == str(people["C"].id)


def test_previous_manager_access_is_admin_and_tenant_scoped(
    db: Session, tenant: SetupTenant, client: TestClient, people: dict[str, Employee]
) -> None:
    url = f"{tenant.url}/roster/employees/{people['B'].id}/previous-manager"
    for role in ("employee", "manager"):
        assert client.get(url, headers=tenant.auth[role]).status_code == 403
    assert client.get(url, headers=tenant.auth["foreign"]).status_code == 404
    foreign = db.exec(
        select(Employee).where(Employee.org_id == tenant.foreign_org_id)
    ).one()
    assert (
        client.get(
            f"{tenant.url}/roster/employees/{foreign.id}/previous-manager"
        ).status_code
        == 404
    )
    assert people["B"].employment_status == EmploymentStatus.ACTIVE
