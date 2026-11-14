import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.domains.org.models import Employee


@pytest.mark.parametrize("role", ["admin", "manager", "employee"])
def test_visible_employees_include_employee_code(
    shared_demo: tuple, client: TestClient, role: str
) -> None:
    url, employees, auth = shared_demo
    response = client.get(f"{url}/employees", headers=auth[role])
    assert response.status_code == 200
    rows = response.json()["data"]
    assert rows
    expected = {str(e.id): e.employee_code for e in employees.values()}
    for row in rows:
        assert row["employee_code"] == expected[row["id"]]
        for suffix in ("", "/profile"):
            detail = client.get(
                f"{url}/employees/{row['id']}{suffix}", headers=auth[role]
            )
            assert detail.status_code == 200
            data = detail.json()
            summary = data["employee"] if suffix else data
            assert summary["employee_code"] == row["employee_code"]
    if role == "employee":
        assert [row["id"] for row in rows] == [str(employees["E02"].id)]
        own = client.get(f"{url}/me/profile", headers=auth[role])
        assert own.status_code == 200
        assert own.json()["employee"]["employee_code"] == "E02"


@pytest.mark.parametrize(
    "role,denied_code", [("manager", "E05"), ("employee", "E03"), ("foreign", "E02")]
)
def test_employee_codes_do_not_expand_visibility(
    shared_demo: tuple, client: TestClient, role: str, denied_code: str
) -> None:
    url, employees, auth = shared_demo
    denied_id = str(employees[denied_code].id)
    response = client.get(f"{url}/employees", headers=auth[role])
    if role == "foreign":
        assert response.status_code == 404
    else:
        assert response.status_code == 200
        assert denied_id not in {row["id"] for row in response.json()["data"]}
    for suffix in ("", "/profile"):
        assert (
            client.get(
                f"{url}/employees/{denied_id}{suffix}", headers=auth[role]
            ).status_code
            == 404
        )


def test_employee_without_code_remains_readable(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    employee = Employee(
        org_id=employees["E02"].org_id,
        department_id=employees["E02"].department_id,
        display_name="合成无工号员工",
    )
    db.add(employee)
    db.commit()
    listing = client.get(f"{url}/employees", headers=auth["manager"])
    assert listing.status_code == 200
    summary = next(
        row for row in listing.json()["data"] if row["id"] == str(employee.id)
    )
    assert summary["employee_code"] is None
    for suffix in ("", "/profile"):
        response = client.get(
            f"{url}/employees/{employee.id}{suffix}", headers=auth["manager"]
        )
        assert response.status_code == 200
        data = response.json()
        assert (data["employee"] if suffix else data)["employee_code"] is None
