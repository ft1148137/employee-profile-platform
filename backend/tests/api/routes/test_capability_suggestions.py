from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.audit.models import AuditEvent
from app.domains.capability.models import CapabilityProposal, CapabilityProposalEvidence
from app.domains.capability.proposals import name_key
from app.domains.evidence.models import Evidence
from app.domains.org.models import EmploymentStatus, OrgMembership
from app.domains.profile.models import ProfileVersion

BODY = {"name": "研发费用归集", "description": "归集研发费用", "reason": "工作需要"}


@pytest.mark.parametrize("role", ["employee", "manager"])
def test_employee_suggests_missing_capability(
    demo: tuple, client: TestClient, db: Session, role: str
) -> None:
    url, employees, auth = demo
    org = employees["E02"].org_id
    before = {
        model: len(db.exec(select(model).where(model.org_id == org)).all())
        for model in (Evidence, ProfileVersion, CapabilityProposalEvidence)
    }
    response = client.post(
        f"{url}/capabilities/proposals", headers=auth[role], json=BODY
    )
    assert response.status_code == 200, response.text
    row = db.exec(
        select(CapabilityProposal).where(CapabilityProposal.org_id == org)
    ).one()
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org,
            OrgMembership.employee_id == row.proposed_by_employee_id,
        )
    ).one()
    assert row.proposed_by_employee_id is not None
    event = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org,
            AuditEvent.action == "capability_proposal.suggested",
        )
    ).one()
    assert event.target_id == str(row.id)
    assert event.actor_id == str(member.user_id) and event.details == {}
    listed = client.get(f"{url}/capabilities/proposals", headers=auth["admin"]).json()
    assert listed["data"][0]["proposed_by_employee_name"] == next(
        e.display_name
        for e in employees.values()
        if e.id == row.proposed_by_employee_id
    )
    assert listed["data"][0]["evidence_count"] == 0
    for model, count in before.items():
        assert len(db.exec(select(model).where(model.org_id == org)).all()) == count


@pytest.mark.parametrize("status", ["open", "accepted", "dismissed", "dictionary"])
def test_duplicate_names_do_not_create_or_disclose_other_authors(
    demo: tuple, client: TestClient, db: Session, status: str
) -> None:
    url, employees, auth = demo
    org = employees["E02"].org_id
    body = dict(BODY)
    if status == "dictionary":
        body["name"] = client.get(
            f"{url}/capabilities", headers=auth["employee"]
        ).json()["data"][0]["name"]
    else:
        db.add(
            CapabilityProposal(
                org_id=org,
                **body,
                name_key=name_key(body["name"]),
                status=status,
                proposed_by_employee_id=employees["E03"].id,
            )
        )
        db.commit()
    response = client.post(
        f"{url}/capabilities/proposals",
        headers=auth["employee"],
        json={**body, "name": f" {body['name'].upper()} "},
    )
    assert response.status_code == 200
    assert list(response.json()) == ["message"]
    expected = {
        "open": "已提交给企业管理员",
        "accepted": "已加入词典",
        "dismissed": "决定暂不收录",
        "dictionary": "词典里已经有",
    }[status]
    assert expected in response.json()["message"]
    rows = db.exec(
        select(CapabilityProposal).where(CapabilityProposal.org_id == org)
    ).all()
    assert len(rows) == (0 if status == "dictionary" else 1)
    assert employees["E03"].display_name not in response.text
    assert not db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org,
            AuditEvent.action == "capability_proposal.suggested",
        )
    ).all()


def test_limit_is_per_employee_pending_and_serialized(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    for i in range(4):
        assert (
            client.post(
                f"{url}/capabilities/proposals",
                headers=auth["employee"],
                json={**BODY, "name": f"建议{i}"},
            ).status_code
            == 200
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda i: client.post(
                    f"{url}/capabilities/proposals",
                    headers=auth["employee"],
                    json={**BODY, "name": f"并发建议{i}"},
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in responses) == [200, 429]
    assert "5 条待处理" in next(r.text for r in responses if r.status_code == 429)
    assert (
        client.post(
            f"{url}/capabilities/proposals", headers=auth["manager"], json=BODY
        ).status_code
        == 200
    )
    # A duplicate does not consume another employee's quota, including at the limit.
    assert (
        client.post(
            f"{url}/capabilities/proposals", headers=auth["employee"], json=BODY
        ).status_code
        == 200
    )
    row = db.exec(
        select(CapabilityProposal).where(
            CapabilityProposal.org_id == employees["E02"].org_id,
            CapabilityProposal.name == "建议0",
        )
    ).one()
    assert (
        client.post(
            f"{url}/capabilities/proposals/{row.id}/dismiss", headers=auth["admin"]
        ).status_code
        == 204
    )
    assert (
        client.post(
            f"{url}/capabilities/proposals",
            headers=auth["employee"],
            json={**BODY, "name": "新建议"},
        ).status_code
        == 200
    )


def test_nonemployees_departed_and_foreign_access_rejected(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    assert (
        client.post(
            f"{url}/capabilities/proposals", headers=auth["admin"], json=BODY
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{url}/capabilities/proposals", headers=auth["foreign"], json=BODY
        ).status_code
        == 404
    )
    assert client.post(f"{url}/capabilities/proposals", json=BODY).status_code == 401
    assert (
        client.post(
            f"{url}/capabilities/proposals", headers=auth["employee"], json=BODY
        ).status_code
        == 200
    )
    for role, status in (("employee", 403), ("manager", 403), ("foreign", 404)):
        assert (
            client.get(f"{url}/capabilities/proposals", headers=auth[role]).status_code
            == status
        )
    employees["E02"].employment_status = EmploymentStatus.INACTIVE
    db.add(employees["E02"])
    db.commit()
    assert (
        client.post(
            f"{url}/capabilities/proposals", headers=auth["employee"], json=BODY
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", " "),
        ("name", "x" * 41),
        ("description", "x" * 301),
        ("reason", "x" * 301),
        ("proposed_by_employee_id", "forged"),
    ],
)
def test_suggestion_validation(
    shared_demo: tuple, client: TestClient, field: str, value: str
) -> None:
    url, _, auth = shared_demo
    assert (
        client.post(
            f"{url}/capabilities/proposals",
            headers=auth["employee"],
            json={**BODY, field: value},
        ).status_code
        == 422
    )


def test_suggestions_are_isolated_between_tenants(
    demo: tuple, client: TestClient, db: Session
) -> None:
    from app.domains.org.models import Employee, Role, RoleGrant

    url, employees, auth = demo
    foreign_member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id
            == UUID(
                client.get("/api/v1/me/memberships", headers=auth["foreign"]).json()[0][
                    "org"
                ]["id"]
            )
        )
    ).one()
    employee = Employee(org_id=foreign_member.org_id, display_name="合成企业员工")
    db.add(employee)
    db.flush()
    foreign_member.employee_id = employee.id
    db.add(foreign_member)
    db.add(
        RoleGrant(
            org_id=employee.org_id, membership_id=foreign_member.id, role=Role.ORG_ADMIN
        )
    )
    db.commit()
    foreign_url = f"/api/v1/orgs/{employee.org_id}/capabilities/proposals"
    assert (
        client.post(
            f"{url}/capabilities/proposals", headers=auth["employee"], json=BODY
        ).status_code
        == 200
    )
    assert client.get(foreign_url, headers=auth["foreign"]).json() == {"data": []}
    assert (
        client.post(foreign_url, headers=auth["foreign"], json=BODY).status_code == 200
    )
    own = client.get(f"{url}/capabilities/proposals", headers=auth["admin"]).json()[
        "data"
    ][0]
    foreign = client.get(foreign_url, headers=auth["foreign"]).json()["data"][0]
    assert own["id"] != foreign["id"]
    assert own["proposed_by_employee_name"] != foreign["proposed_by_employee_name"]
    assert (
        client.post(
            f"{foreign_url}/{own['id']}/dismiss", headers=auth["foreign"]
        ).status_code
        == 404
    )
