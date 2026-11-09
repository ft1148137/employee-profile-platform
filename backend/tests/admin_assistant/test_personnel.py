import csv
from dataclasses import replace
from io import StringIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.authz.deps import get_org_principal
from app.authz.principal import Principal
from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import registry, store
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import ProposeChanges
from app.domains.change_request.models import (
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.org.models import (
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
    ReportingLine,
)
from app.domains.org.roster import single
from app.domains.org.roster.diff import compute_roster_diff
from app.domains.org.roster.schemas import HEADERS
from app.domains.org.service import EmployeeNotFound
from app.models import User
from tests.api.routes.test_roster import apply, employees, link, upload
from tests.api.routes.test_roster import client as client
from tests.api.routes.test_setup_steps import SetupTenant
from tests.api.routes.test_setup_steps import tenant as tenant

FUNCTIONS = {
    "onboard": "onboard_employee",
    "transfer": "transfer_employee",
    "offboard": "offboard_employees",
}


@pytest.mark.parametrize("protected", ["shared", "superuser"])
@pytest.mark.parametrize("via", ["web", "assistant"])
def test_offboard_cannot_disable_protected_account(
    db: Session,
    tenant: SetupTenant,
    client: TestClient,
    roster: dict,
    protected: str,
    via: str,
) -> None:
    account = link(db, tenant, roster["B"])
    if protected == "shared":
        db.add(OrgMembership(org_id=tenant.foreign_org_id, user_id=account.id))
    else:
        account.is_superuser = True
        db.add(account)
    db.commit()
    body = {"kind": "offboard", "employee_ids": [str(roster["B"].id)]}
    if via == "web":
        preview = client.post(f"{tenant.url}/roster/single-change", json=body).json()
        assert not preview["can_apply"]
        assert apply(client, tenant, preview).status_code == 422
    else:
        row = propose(db, tenant, body)
        with pytest.raises(c.ChangeSetError):
            c.apply(db, principal(db, tenant), row.id, row.revision)
    db.refresh(account)
    assert account.is_active
    assert employees(db, tenant)["B"].employment_status == EmploymentStatus.ACTIVE


def test_personnel_rolls_back_with_later_failure(
    db: Session, tenant: SetupTenant, roster: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = principal(db, tenant)
    before = single.current_roster(db, p).model_dump()
    revision = db.get_one(Organization, tenant.org_id).roster_revision
    row = c.propose(
        db,
        p,
        store.create_session(db, p).id,
        ProposeChanges.model_validate(
            {
                "items": [
                    {
                        "function": "transfer_employee",
                        "args": {"employee_id": str(roster["B"].id), "title": "新职位"},
                    },
                    {
                        "function": "update_update_settings",
                        "args": {"reminder_schedule": "yearly"},
                    },
                ]
            }
        ),
    )
    db.commit()

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic failure")

    monkeypatch.setitem(
        registry.FUNCTIONS,
        "update_update_settings",
        replace(registry.FUNCTIONS["update_update_settings"], handler=fail),
    )
    with pytest.raises(c.ChangeSetError, match="所有改动均已回滚"):
        c.apply(db, p, row.id, row.revision)
    assert single.current_roster(db, p).model_dump() == before
    assert db.get_one(Organization, tenant.org_id).roster_revision == revision
    assert c.get(db, p, row.id).status == "draft"


@pytest.fixture
def roster(db: Session, tenant: SetupTenant, client: TestClient) -> dict[str, Employee]:
    content = "工号,姓名,部门,职位,上级工号,邮箱\nA,甲,研发,经理,,a@example.com\nB,乙,研发,工程师,A,b@example.com\nC,丙,运营,专员,,c@example.com\n".encode()
    assert apply(client, tenant, upload(client, tenant, content)).status_code == 200
    db.add(
        Employee(
            org_id=tenant.org_id,
            employee_code="OLD",
            display_name="已离职",
            employment_status=EmploymentStatus.INACTIVE,
        )
    )
    db.commit()
    return employees(db, tenant)


def principal(db: Session, tenant: SetupTenant) -> Principal:
    return get_org_principal(tenant.org_id, db.get_one(User, tenant.admin_id), db)


def propose(db: Session, tenant: SetupTenant, body: dict) -> AdminChangeSet:
    p = principal(db, tenant)
    row = c.propose(
        db,
        p,
        store.create_session(db, p).id,
        ProposeChanges.model_validate(
            {
                "items": [
                    {
                        "function": FUNCTIONS[body["kind"]],
                        "args": body,
                    }
                ]
            }
        ),
    )
    db.commit()
    return row


def test_unchanged_roster(db: Session, tenant: SetupTenant, roster: dict) -> None:
    assert roster["OLD"].employment_status == EmploymentStatus.INACTIVE
    rebuilt = single.current_roster(db, principal(db, tenant))
    assert any(r.inactive for r in rebuilt.rows)
    assert compute_roster_diff(db, tenant.org_id, rebuilt).changes == []


@pytest.mark.parametrize("kind", ["onboard", "transfer", "offboard"])
@pytest.mark.parametrize("via", ["web", "assistant"])
def test_matches_complete_upload(
    db: Session,
    tenant: SetupTenant,
    client: TestClient,
    roster: dict,
    kind: str,
    via: str,
) -> None:
    body = {
        "onboard": {
            "kind": "onboard",
            "employee_code": "D",
            "display_name": "丁",
            "email": "d@example.com",
            "department": "研发/平台",
            "title": "开发",
            "manager_id": str(roster["A"].id),
        },
        "transfer": {
            "kind": "transfer",
            "employee_id": str(roster["B"].id),
            "department": "运营",
            "title": "主管",
            "manager_id": str(roster["C"].id),
        },
        "offboard": {"kind": "offboard", "employee_ids": [str(roster["B"].id)]},
    }[kind]
    # Independent full-file fixtures assert parity with the existing public route.
    rows = [
        ["A", "甲", "研发", "经理", "", "a@example.com"],
        ["B", "乙", "研发", "工程师", "A", "b@example.com"],
        ["C", "丙", "运营", "专员", "", "c@example.com"],
    ]
    if kind == "onboard":
        rows.append(["D", "丁", "研发/平台", "开发", "A", "d@example.com"])
    elif kind == "transfer":
        rows[1] = ["B", "乙", "运营", "主管", "C", "b@example.com"]
    else:
        rows.pop(1)
    out = StringIO()
    writer = csv.writer(out)
    writer.writerows([HEADERS, *rows])
    expected = upload(client, tenant, out.getvalue().encode())
    if via == "web":
        response = client.post(f"{tenant.url}/roster/single-change", json=body)
        assert response.status_code == 200, response.text
        actual = response.json()
        assert [
            {k: v for k, v in change.items() if k != "row_number"}
            for change in actual["changes"]
        ] == [
            {k: v for k, v in change.items() if k != "row_number"}
            for change in expected["changes"]
        ]
        assert actual["summary"] == expected["summary"]
        assert apply(client, tenant, actual).status_code == 200
    else:
        row = propose(db, tenant, body)
        result = c.apply(db, principal(db, tenant), row.id, row.revision)
        assert result.status == "applied"
        assert result.items[0].title and not result.items[0].undoable
        assert "title" not in row.items[0]
        with pytest.raises(c.ChangeSetError, match="不能撤销"):
            c.undo(db, principal(db, tenant), row.id)
    actual_employees = employees(db, tenant)
    assert actual_employees["OLD"].employment_status == EmploymentStatus.INACTIVE
    if kind == "offboard":
        assert actual_employees["B"].employment_status == EmploymentStatus.INACTIVE
    else:
        target = actual_employees["D" if kind == "onboard" else "B"]
        assert target.title == ("开发" if kind == "onboard" else "主管")
        manager = db.exec(
            select(ReportingLine).where(
                ReportingLine.org_id == tenant.org_id,
                ReportingLine.employee_id == target.id,
                col(ReportingLine.valid_to).is_(None),
            )
        ).one()
        assert (
            manager.manager_employee_id == roster["A" if kind == "onboard" else "C"].id
        )


@pytest.mark.parametrize("via", ["web", "assistant"])
def test_stale(
    db: Session, tenant: SetupTenant, client: TestClient, roster: dict, via: str
) -> None:
    body = {"kind": "offboard", "employee_ids": [str(roster["B"].id)]}
    row = propose(db, tenant, body) if via == "assistant" else None
    preview = client.post(f"{tenant.url}/roster/single-change", json=body).json()
    org = db.get_one(Organization, tenant.org_id)
    org.roster_revision += 1
    db.add(org)
    db.commit()
    if row:
        with pytest.raises(c.ChangeSetError, match="内容已变化"):
            c.apply(db, principal(db, tenant), row.id, row.revision)
        assert c.get(db, principal(db, tenant), row.id).status == "stale"
    else:
        assert apply(client, tenant, preview).status_code == 409


def test_risk_and_impact(db: Session, tenant: SetupTenant, roster: dict) -> None:
    db.add(
        ProfileChangeRequest(
            org_id=tenant.org_id,
            subject_employee_id=roster["B"].id,
            origin=RequestOrigin.MANUAL,
            status=RequestStatus.PENDING_REVIEW,
            author={},
        )
    )
    db.commit()
    row = propose(
        db,
        tenant,
        {"kind": "offboard", "employee_ids": [str(roster[c].id) for c in ["B", "C"]]},
    )
    assert row.items[0]["risk"] == "high"
    assert row.items[0]["impact"]["count"] == 2
    assert "1 个待处理" in row.items[0]["impact"]["text"]
    p = principal(db, tenant)
    assert c.apply(db, p, row.id, row.revision).status == "draft"
    assert employees(db, tenant)["B"].employment_status == EmploymentStatus.ACTIVE
    assert c.apply(db, p, row.id, row.revision).status == "applied"
    assert employees(db, tenant)["C"].employment_status == EmploymentStatus.INACTIVE


def test_partial_transfer_and_boundaries(
    db: Session, tenant: SetupTenant, client: TestClient, roster: dict
) -> None:
    body = {
        "kind": "transfer",
        "employee_id": str(roster["B"].id),
        "department": "新部门",
    }
    row = propose(db, tenant, body)
    c.apply(db, principal(db, tenant), row.id, row.revision)
    assert employees(db, tenant)["B"].title == "工程师"
    assert (
        db.exec(
            select(ReportingLine).where(
                ReportingLine.org_id == tenant.org_id,
                ReportingLine.employee_id == roster["B"].id,
                col(ReportingLine.valid_to).is_(None),
            )
        )
        .one()
        .manager_employee_id
        == roster["A"].id
    )
    foreign = db.exec(
        select(Employee).where(Employee.org_id == tenant.foreign_org_id)
    ).one()
    for employee_id in [foreign.id, uuid4()]:
        denied = {**body, "employee_id": str(employee_id)}
        assert (
            client.post(f"{tenant.url}/roster/single-change", json=denied).status_code
            == 404
        )
        with pytest.raises(EmployeeNotFound) as error:
            propose(db, tenant, denied)
        assert error.value.status_code == 404
    for role in ["employee", "manager", "foreign"]:
        assert client.post(
            f"{tenant.url}/roster/single-change", json=body, headers=tenant.auth[role]
        ).status_code in [403, 404]
