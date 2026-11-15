import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    OrgMembership,
    RoleGrant,
)
from tests.api.routes.test_m5_coverage import coverage_data as coverage_data


@pytest.fixture
def regional_coverage(db: Session, coverage_data: tuple) -> tuple:
    org, *_ = coverage_data
    departments = {
        d.name: d
        for d in db.exec(select(Department).where(Department.org_id == org.id)).all()
    }
    grandchild = Department(
        org_id=org.id,
        name="门店下级组",
        parent_department_id=departments["研发子部门"].id,
    )
    db.add(grandchild)
    db.flush()
    db.add_all(
        [
            Employee(
                org_id=org.id, department_id=grandchild.id, display_name="合成组员"
            ),
            Employee(
                org_id=org.id,
                department_id=departments["空部门"].id,
                display_name="合成另一门店员工",
            ),
            Employee(
                org_id=org.id,
                department_id=departments["空部门"].id,
                display_name="合成离职员工",
                employment_status=EmploymentStatus.INACTIVE,
            ),
        ]
    )
    db.commit()
    return coverage_data


def test_regional_summary_preserves_direct_counts_and_overall_total(
    client: TestClient, regional_coverage: tuple
) -> None:
    org, _, headers, *_ = regional_coverage
    for role, total in [("manager", 7), ("admin", 9)]:
        response = client.get(f"/api/v1/orgs/{org.id}/coverage", headers=headers[role])
        assert response.status_code == 200
        result = response.json()
        assert result["total"] == total
        departments = {d["department_name"]: d for d in result["departments"]}
        region = departments["研发"]
        assert region["total"] == 2
        assert departments["研发子部门"]["total"] == 3
        assert departments["空部门"]["total"] == 1
        summary = region["with_descendants"]
        assert summary["total"] == 7
        assert {s["status"]: s["count"] for s in summary["statuses"]} == {
            "published": 1,
            "disputed": 1,
            "pending_employee": 1,
            "pending_manager": 1,
            "none": 3,
        }
        assert summary["statuses"][0]["percentage"] == 42.9
        assert departments["研发子部门"]["with_descendants"]["total"] == 4
        assert departments["空部门"]["with_descendants"] is None
        assert departments["门店下级组"]["with_descendants"] is None
        assert {s["status"]: s["count"] for s in result["statuses"]} == {
            "published": 1,
            "disputed": 1,
            "pending_employee": 1,
            "pending_manager": 1 if role == "manager" else 2,
            "none": 3 if role == "manager" else 4,
        }
        assert "PRIVATE_" not in response.text


def test_store_manager_cannot_see_ancestors_or_other_stores(
    client: TestClient, db: Session, regional_coverage: tuple
) -> None:
    org, people, headers, principals, *_ = regional_coverage
    store = db.exec(
        select(Department).where(
            Department.org_id == org.id, Department.name == "空部门"
        )
    ).one()
    grant = db.exec(
        select(RoleGrant).where(
            RoleGrant.org_id == org.id,
            RoleGrant.department_id == people["published"].department_id,
        )
    ).one()
    grant.department_id = store.id
    db.add(grant)
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org.id,
            OrgMembership.user_id == principals["manager"].user_id,
        )
    ).one()
    membership.employee_id = (
        db.exec(
            select(Employee).where(
                Employee.org_id == org.id, Employee.display_name == "合成另一门店员工"
            )
        )
        .one()
        .id
    )
    db.add(membership)
    db.commit()
    response = client.get(f"/api/v1/orgs/{org.id}/coverage", headers=headers["manager"])
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 1
    assert [d["department_id"] for d in result["departments"]] == [str(store.id)]
    assert result["departments"][0]["with_descendants"] is None
    assert {s["status"]: s["count"] for s in result["statuses"]} == {
        "published": 0,
        "disputed": 0,
        "pending_employee": 0,
        "pending_manager": 0,
        "none": 1,
    }
