import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.base import utc_now
from app.domains.org.models import (
    Employee,
    EmploymentStatus,
    OrgMembership,
    ReportingLine,
    Role,
    RoleGrant,
)
from tests.authz.test_managed_scope import hierarchy as hierarchy
from tests.authz.test_tenant_access import headers


@pytest.fixture
def chart_data(hierarchy: tuple, db: Session) -> tuple:
    orgs, departments, employees, users, _ = hierarchy
    memberships = [
        db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == orgs[0].id,
                OrgMembership.user_id == user.id,
            )
        ).one()
        for user in users[:2]
    ]
    memberships[1].employee_id = employees[0][1].id
    db.add(memberships[1])
    db.add(
        RoleGrant(
            org_id=orgs[0].id, membership_id=memberships[0].id, role=Role.ORG_ADMIN
        )
    )
    db.add_all(
        [
            Employee(
                org_id=orgs[0].id,
                department_id=departments[0][2].id,
                display_name="合成离职员工",
                employment_status=EmploymentStatus.INACTIVE,
            ),
            Employee(org_id=orgs[0].id, display_name="无部门员工"),
            ReportingLine(
                org_id=orgs[0].id,
                employee_id=employees[0][2].id,
                manager_employee_id=employees[0][1].id,
            ),
            ReportingLine(
                org_id=orgs[0].id,
                employee_id=employees[0][2].id,
                manager_employee_id=employees[0][3].id,
                valid_to=utc_now(),
            ),
            ReportingLine(
                org_id=orgs[0].id,
                employee_id=employees[0][1].id,
                manager_employee_id=employees[0][0].id,
            ),
        ]
    )
    db.commit()
    return hierarchy


@pytest.mark.parametrize(
    "actor,visible,totals",
    [
        (0, {0, 1, 2, 3}, [3, 2, 1, 1]),
        (1, {1, 2}, [0, 2, 1, 0]),
        (2, {3}, [0, 0, 0, 1]),
    ],
)
def test_chart_scope_counts_and_managers(
    chart_data: tuple,
    client: TestClient,
    actor: int,
    visible: set[int],
    totals: list[int],
) -> None:
    orgs, departments, employees, users, _ = chart_data
    response = client.get(
        f"/api/v1/orgs/{orgs[0].id}/org-chart", headers=headers(users[actor])
    )
    assert response.status_code == 200
    nodes = {row["id"]: row for row in response.json()["departments"]}
    assert set(nodes) == {str(departments[0][i].id) for i in visible}
    for i in visible:
        node = nodes[str(departments[0][i].id)]
        assert node["direct_count"] == 1
        assert node["total_count"] == totals[i]
        parent = departments[0][i].parent_department_id
        assert node["parent_department_id"] == (
            str(parent) if str(parent) in nodes else None
        )
    people = {row["id"]: row for row in response.json()["people"]}
    expected = {
        str(employee.id)
        for employee in employees[0]
        if employee.department_id in {departments[0][i].id for i in visible}
    }
    if actor == 0:
        expected.add(
            next(row["id"] for row in people.values() if row["department_id"] is None)
        )
    assert set(people) == expected
    assert all(row["display_name"] != "合成离职员工" for row in people.values())
    assert not set(people) & {str(employee.id) for employee in employees[1]}
    for row in people.values():
        assert row["manager_id"] is None or row["manager_id"] in people
    if actor in (0, 1):
        assert people[str(employees[0][2].id)]["manager_id"] == str(employees[0][1].id)
        assert people[str(employees[0][1].id)]["manager_id"] == (
            str(employees[0][0].id) if actor == 0 else None
        )
    if 1 in visible:
        assert {
            "employee_id": str(employees[0][1].id),
            "display_name": employees[0][1].display_name,
        } in nodes[str(departments[0][1].id)]["managers"]
    if actor == 0:
        assert nodes[str(departments[0][0].id)]["managers"] == [
            {"employee_id": None, "display_name": users[0].username}
        ]


def test_chart_employee_and_tenant_boundaries(
    shared_demo: tuple, client: TestClient
) -> None:
    url, _, auth = shared_demo
    assert client.get(url + "/org-chart", headers=auth["employee"]).status_code == 403
    assert client.get(url + "/org-chart", headers=auth["foreign"]).status_code == 404


def test_department_people_and_current_reporting_scope(
    chart_data: tuple, client: TestClient, db: Session
) -> None:
    orgs, departments, employees, users, _ = chart_data
    orgs[0].employee_fields = ["job_family"]
    employees[0][2].job_family = "合成岗位序列"
    db.add_all([orgs[0], employees[0][2]])
    db.commit()
    url = f"/api/v1/orgs/{orgs[0].id}"
    for actor in (0, 1):
        auth = headers(users[actor])
        people = client.get(
            url + "/employees",
            params={
                "department_id": str(departments[0][2].id),
                "employment_status": "active",
            },
            headers=auth,
        ).json()
        assert people["count"] == 1
        employee = people["data"][0]
        assert employee["id"] == str(employees[0][2].id)
        assert employee["job_family"] == "合成岗位序列"
        assert employee["manager"] == {
            "id": str(employees[0][1].id),
            "display_name": employees[0][1].display_name,
        }
        assert (
            client.get(url + f"/employees/{employee['id']}", headers=auth).json()
            == employee
        )
        manager = client.get(
            url + f"/employees/{employees[0][1].id}", headers=auth
        ).json()["manager"]
        assert (manager is None) == (actor == 1)
    for department in (departments[0][0], departments[0][3], departments[1][0]):
        response = client.get(
            url + "/employees",
            params={"department_id": str(department.id)},
            headers=headers(users[1]),
        )
        assert response.status_code == 200
        assert response.json() == {"data": [], "count": 0}


def test_chart_hierarchy_cycle_terminates(
    chart_data: tuple, db: Session, client: TestClient
) -> None:
    orgs, departments, _, users, _ = chart_data
    root = departments[0][0]
    root.parent_department_id = departments[0][2].id
    db.add(root)
    db.commit()
    response = client.get(
        f"/api/v1/orgs/{orgs[0].id}/org-chart", headers=headers(users[0])
    )
    assert response.status_code == 200
    nodes = {node["id"]: node for node in response.json()["departments"]}
    for node in nodes.values():
        seen = set()
        while node:
            assert node["id"] not in seen
            seen.add(node["id"])
            node = nodes.get(node["parent_department_id"])


def test_chart_reporting_cycle_returns_people(
    chart_data: tuple, db: Session, client: TestClient
) -> None:
    orgs, _, employees, users, _ = chart_data
    db.add(
        ReportingLine(
            org_id=orgs[0].id,
            employee_id=employees[0][0].id,
            manager_employee_id=employees[0][2].id,
        )
    )
    db.commit()
    response = client.get(
        f"/api/v1/orgs/{orgs[0].id}/org-chart", headers=headers(users[0])
    )
    assert response.status_code == 200
    people = {row["id"]: row for row in response.json()["people"]}
    assert people[str(employees[0][0].id)]["manager_id"] == str(employees[0][2].id)
    assert people[str(employees[0][2].id)]["manager_id"] == str(employees[0][1].id)
