from functools import partial
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from openpyxl import load_workbook
from sqlalchemy import event
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.config import settings
from app.core.db import engine
from app.domains.account.passwords import ALPHABET, generate_password
from app.domains.account.service import list_accounts
from app.domains.audit.models import ActorKind, AuditEvent
from app.domains.org.models import Employee, EmploymentStatus, OrgMembership
from app.models import User
from tests.authz.test_tenant_access import headers, tenant_data  # noqa: F401


@pytest.fixture
def accounts(request: pytest.FixtureRequest, db: Session) -> tuple:
    orgs, depts, employees, users = request.getfixturevalue("tenant_data")
    for i, employee in enumerate(employees):
        employee.employee_code = f"{uuid4().hex[:8]}-{i}"
        db.add(employee)
    employees[0].display_name, employees[3].display_name = ("=1+1", "@SUM(A1)")
    employees[0].work_email = str(users[0].email).upper()
    db.commit()
    return (
        f"/api/v1/orgs/{orgs[0].id}",
        headers(users[0]),
        orgs,
        depts,
        employees,
        users,
    )


def test_issue_and_reset(accounts: tuple, client: TestClient, db: Session) -> None:
    url, auth, orgs, _, employees, users = accounts
    post = partial(client.post, headers=auth)
    for _ in range(30):
        password = generate_password()
        assert len(password) == 12 and set(password) <= set(ALPHABET)
        assert any(c.isdigit() for c in password) and any(c.isalpha() for c in password)
    response = post(f"{url}/accounts/issue", json={})
    assert response.status_code == 200 and response.headers["x-issued-count"] == "3"
    assert response.headers["cache-control"] == "no-store"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet is not None and sheet.max_row == 4
    rows = list(sheet.values)
    assert rows[0] == ("工号", "姓名", "部门", "登录地址", "初始密码", "备注")
    assert all(cell.data_type == "s" for row in sheet for cell in row if cell.value)
    assert {row[1] for row in rows[1:]} >= {"=1+1", "@SUM(A1)"}
    first = next(row for row in rows if row[0] == employees[0].employee_code)
    assert "邮箱已被其他账号使用" in first[5]

    def login(password: str) -> Response:
        return post(
            "/api/v1/login/access-token",
            data={"username": first[0], "password": password},
        )

    logged_in = login(first[4])
    assert logged_in.status_code == 200
    me = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {logged_in.json()['access_token']}"},
    )
    assert me.json()["must_change_password"] is True
    restricted = client.get(
        "/api/v1/me/memberships",
        headers={"Authorization": f"Bearer {logged_in.json()['access_token']}"},
    )
    assert restricted.status_code == 403
    assert restricted.json()["detail"]["code"] == "password_change_required"
    user = db.exec(select(User).where(User.username == first[0])).one()
    assert (
        user.must_change_password and user.password_changed_at and (user.email is None)
    )
    assert post(f"{url}/accounts/issue", json={}).status_code == 422
    selected = {"employee_ids": [str(employees[0].id)], "regenerate_pending": True}
    regenerated = post(f"{url}/accounts/issue", json=selected)
    assert (
        regenerated.status_code == 200 and regenerated.headers["x-issued-count"] == "1"
    )
    assert login(first[4]).status_code == 400
    regenerated_sheet = load_workbook(BytesIO(regenerated.content)).active
    assert regenerated_sheet is not None
    initial_password = list(regenerated_sheet.values)[1][4]
    previous_session = login(initial_password)
    assert previous_session.status_code == 200
    previous_auth = {
        "Authorization": f"Bearer {previous_session.json()['access_token']}"
    }
    assert client.get("/api/v1/users/me", headers=previous_auth).status_code == 200
    for _ in range(settings.LOGIN_MAX_FAILURES):
        assert login("wrong-password").status_code == 400
    assert login(initial_password).status_code == 429
    reset = post(f"{url}/accounts/{user.id}/reset-password")
    assert reset.status_code == 200 and reset.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/users/me", headers=previous_auth).status_code == 401
    password = reset.json()["initial_password"]
    assert login(password).status_code == 200
    assert post(f"{url}/accounts/{user.id}/disable").status_code == 200
    assert login(password).status_code == 400
    assert post(f"{url}/accounts/{user.id}/reset-password").status_code == 200
    db.refresh(user)
    assert not user.is_active
    employees[0].employment_status = EmploymentStatus.INACTIVE
    db.add(employees[0])
    db.commit()
    assert post(f"{url}/accounts/{user.id}/enable").status_code == 409
    employees[0].employment_status = EmploymentStatus.ACTIVE
    db.add(employees[0])
    db.commit()
    assert post(f"{url}/accounts/{user.id}/enable").status_code == 200
    user.must_change_password = False
    db.add(user)
    db.commit()
    assert post(f"{url}/accounts/issue", json=selected).status_code == 409
    events = db.exec(select(AuditEvent).where(AuditEvent.org_id == orgs[0].id)).all()
    assert {
        "account.issued",
        "account.password_reset",
        "account.disabled",
        "account.enabled",
    } <= {e.action for e in events}
    assert all(set(e.details) == {"id"} for e in events)
    assert all(
        password not in str(e.model_dump()) and first[4] not in str(e.model_dump())
        for e in events
    )
    assert post(f"{url}/accounts/{users[0].id}/reset-password").status_code == 409


def test_conflicts_and_permissions(
    accounts: tuple, client: TestClient, db: Session
) -> None:
    url, auth, _, _, employees, users = accounts
    post = partial(client.post, headers=auth)
    get = partial(client.get, headers=auth)
    employees[0].employee_code = users[0].username.upper()
    employees[4].employment_status = EmploymentStatus.INACTIVE
    db.add_all(employees)
    db.commit()
    batch = {
        "employee_ids": [
            str(employees[0].id),
            str(employees[3].id),
            str(employees[4].id),
        ]
    }
    response = post(f"{url}/accounts/issue", json=batch)
    assert response.status_code == 409 and len(response.json()["detail"]) == 2
    assert not db.exec(
        select(OrgMembership).where(OrgMembership.employee_id == employees[3].id)
    ).first()
    for ids, expected in (
        ([], 422),
        ([str(employees[5].id)], 404),
        ([str(employees[1].id)], 409),
        ([str(employees[4].id)], 409),
    ):
        assert (
            post(f"{url}/accounts/issue", json={"employee_ids": ids}).status_code
            == expected
        )
    employees[0].employee_code = None
    db.add(employees[0])
    db.commit()
    assert (
        post(
            f"{url}/accounts/issue", json={"employee_ids": [str(employees[0].id)]}
        ).status_code
        == 409
    )
    issued = post(f"{url}/accounts/issue", json={})
    assert issued.status_code == 200 and issued.headers["x-issued-count"] == "1"
    for path in ("accounts", "role-grants"):
        assert get(f"{url}/{path}", headers=headers(users[1])).status_code == 403
        assert get(f"{url}/{path}", headers=headers(users[3])).status_code == 404
    for action in ("reset-password", "disable", "enable"):
        assert post(f"{url}/accounts/{users[3].id}/{action}").status_code == 404
        assert (
            post(
                f"{url}/accounts/{users[2].id}/{action}", headers=headers(users[1])
            ).status_code
            == 403
        )
    assert post(f"{url}/accounts/{users[0].id}/disable").status_code == 409
    assert (
        post(f"{url}/accounts/issue", headers=headers(users[1]), json={}).status_code
        == 403
    )


def test_roles_and_visibility(accounts: tuple, client: TestClient, db: Session) -> None:
    url, auth, _, depts, employees, users = accounts
    post = partial(client.post, headers=auth)
    get = partial(client.get, headers=auth)
    delete = partial(client.delete, headers=auth)
    body = {
        "user_id": str(users[2].id),
        "role": "dept_manager",
        "department_id": str(depts[0].id),
    }
    assert get(f"{url}/employees", headers=headers(users[2])).json()["count"] == 1
    grant = post(f"{url}/role-grants", json=body)
    assert grant.status_code == 200
    assert post(f"{url}/role-grants", json=body).json()["id"] == grant.json()["id"]
    assert get(f"{url}/employees", headers=headers(users[2])).json()["count"] == 3
    assert (
        "dept_manager"
        in get("/api/v1/me/memberships", headers=headers(users[2])).json()[0]["roles"]
    )
    assert delete(f"{url}/role-grants/{grant.json()['id']}").status_code == 200
    assert get(f"{url}/employees", headers=headers(users[2])).json()["count"] == 1
    assert (
        "dept_manager"
        not in get("/api/v1/me/memberships", headers=headers(users[2])).json()[0][
            "roles"
        ]
    )
    assert delete(f"{url}/role-grants/{grant.json()['id']}").status_code == 404
    for changed, code in (
        ({"department_id": None}, 422),
        ({"role": "org_admin"}, 422),
        ({"department_id": str(uuid4())}, 404),
        ({"user_id": str(users[3].id)}, 404),
    ):
        assert post(f"{url}/role-grants", json=body | changed).status_code == code
    for active in (False, True):
        users[2].is_active = active
        employees[2].employment_status = EmploymentStatus.INACTIVE
        db.add_all([users[2], employees[2]])
        db.commit()
        assert post(f"{url}/role-grants", json=body).status_code == 409
    admin = next(
        g for g in get(f"{url}/role-grants").json() if g["role"] == "org_admin"
    )
    assert delete(f"{url}/role-grants/{admin['id']}").status_code == 409
    employees[2].employment_status = EmploymentStatus.ACTIVE
    db.add(employees[2])
    db.commit()
    second = post(
        f"{url}/role-grants", json={"user_id": str(users[2].id), "role": "org_admin"}
    )
    assert second.status_code == 200
    assert delete(f"{url}/role-grants/{admin['id']}").status_code == 200
    assert (
        delete(
            f"{url}/role-grants/{second.json()['id']}", headers=headers(users[2])
        ).status_code
        == 409
    )
    assert post(f"{url}/accounts/{users[2].id}/disable").status_code == 403


def test_list_filters_and_query_bound(
    accounts: tuple, client: TestClient, db: Session
) -> None:
    url, auth, orgs, depts, employees, users = accounts
    get = partial(client.get, headers=auth)
    assert get(f"{url}/accounts?status=none").json()["count"] == 3
    assert get(f"{url}/accounts?status=active").json()["count"] == 2
    users[1].is_active = False
    users[2].must_change_password = True
    db.add_all(users)
    db.commit()
    for status in ("disabled", "pending_first_login"):
        assert get(f"{url}/accounts?status={status}").json()["count"] == 1
    assert get(f"{url}/accounts?q={employees[0].employee_code}").json()["count"] == 1
    assert get(f"{url}/accounts?q=%25").json()["count"] == 0
    assert get(f"{url}/accounts?department_id={depts[0].id}").json()["count"] == 2
    assert get(f"{url}/accounts?skip=1&limit=1").json()["count"] == 5
    assert get(f"{url}/accounts?skip=-1").status_code == 422
    db.add_all(
        Employee(org_id=orgs[0].id, display_name=f"Extra {i}") for i in range(96)
    )
    db.commit()
    statements = []

    def count_query(*args: object) -> None:
        statements.append(args[2])

    principal = Principal(
        ActorKind.USER, orgs[0].id, users[0].id, None, True, frozenset()
    )
    with Session(engine) as session:
        event.listen(engine, "before_cursor_execute", count_query)
        try:
            page = list_accounts(session, principal, None, None, None, 0, 100)
        finally:
            event.remove(engine, "before_cursor_execute", count_query)
    assert len(statements) == 3 and page.count == 101 and (len(page.data) == 100)


def test_employment_filter(accounts: tuple, client: TestClient, db: Session) -> None:
    url, auth, _, depts, employees, _ = accounts
    employee = employees[0]
    employee.employment_status = EmploymentStatus.INACTIVE
    db.add(employee)
    db.commit()
    for query, expected in (
        ("employment_status=inactive", 1),
        ("employment_status=active", 4),
        ("employment_status=active&status=none", 2),
        ("employment_status=inactive&status=active", 0),
        (
            f"employment_status=inactive&status=none&department_id={depts[0].id}&q={employee.employee_code}",
            1,
        ),
        (
            f"employment_status=active&status=none&department_id={depts[0].id}&q={employee.employee_code}",
            0,
        ),
    ):
        response = client.get(f"{url}/accounts?{query}", headers=auth)
        assert response.status_code == 200
        assert response.json()["count"] == expected
    assert (
        client.get(
            f"{url}/accounts?employment_status=invalid", headers=auth
        ).status_code
        == 422
    )
    cors = client.get(
        f"{url}/accounts", headers=auth | {"Origin": settings.FRONTEND_HOST}
    )
    assert set(cors.headers["access-control-expose-headers"].split(", ")) == {
        "Content-Disposition",
        "X-Issued-Count",
    }
