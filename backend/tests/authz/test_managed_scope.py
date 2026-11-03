import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlmodel import Session

from app.authz.deps import managed_scope
from app.core.db import engine
from app.domains.base import utc_now
from app.domains.capability.models import CapabilitySchemaVersion
from app.domains.evidence.models import EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence
from app.domains.org.models import (
    Department,
    Employee,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.profile.models import ProfileProgress, ProfileVersion
from app.models import User
from tests.authz.test_tenant_access import headers
from tests.utils.org import delete_test_organization


@pytest.fixture
def hierarchy(db: Session) -> Generator[tuple]:
    orgs = [Organization(slug=str(uuid.uuid7()), name="合成企业") for _ in range(2)]
    users = [
        User(username=str(uuid.uuid7()), hashed_password="unused") for _ in range(4)
    ]
    db.add_all([*orgs, *users])
    db.flush()
    departments = []
    employees = []
    evidence = []
    for org in orgs:
        tree = []
        for name, parent in (
            ("研发中心", None),
            ("平台部", 0),
            ("质量组", 1),
            ("市场中心", None),
        ):
            dept = Department(
                org_id=org.id,
                name=name,
                parent_department_id=tree[parent].id if parent is not None else None,
            )
            db.add(dept)
            db.flush()
            tree.append(dept)
        departments.append(tree)
        staff = [
            Employee(org_id=org.id, department_id=dept.id, display_name=f"合成员工{i}")
            for i, dept in enumerate(tree)
        ]
        db.add_all(staff)
        schema = CapabilitySchemaVersion(org_id=org.id, version=1)
        db.add(schema)
        db.flush()
        employees.append(staff)
        records = []
        for kind, visibility in (
            (EvidenceKind.WORK_RECORD, Visibility.STANDARD),
            (EvidenceKind.SELF_REPORT, Visibility.RESTRICTED),
            (EvidenceKind.MANAGER_REVIEW, Visibility.RESTRICTED),
        ):
            record, _ = append_evidence(
                db,
                org_id=org.id,
                subject_employee_id=staff[2].id,
                kind=kind,
                source="synthetic",
                source_ref=kind.value,
                title="合成证据",
                content={"text": "合成正文"},
                visibility=visibility,
                provenance={},
            )
            records.append(record)
        evidence.append(records)
        db.add(ProfileProgress(org_id=org.id, employee_id=staff[2].id))
        db.add(
            ProfileVersion(
                org_id=org.id,
                subject_employee_id=staff[2].id,
                revision=1,
                schema_version_id=schema.id,
                snapshot={"claims": []},
                published_by={"actor_type": "user"},
                published_at=utc_now(),
            )
        )
    for user, org_index, dept_index in zip(
        users, (0, 0, 0, 1), (0, 1, 3, 0), strict=True
    ):
        membership = OrgMembership(org_id=orgs[org_index].id, user_id=user.id)
        db.add(membership)
        db.flush()
        # Overlapping grants must not duplicate rows or counts.
        for dept in departments[org_index][
            dept_index : dept_index + (2 if dept_index == 0 else 1)
        ]:
            db.add(
                RoleGrant(
                    org_id=orgs[org_index].id,
                    membership_id=membership.id,
                    role=Role.DEPT_MANAGER,
                    department_id=dept.id,
                )
            )
    db.commit()
    try:
        yield orgs, departments, employees, users, evidence
    finally:
        db.rollback()
        for org in orgs:
            delete_test_organization(db, org.id)
        for user in users:
            db.delete(user)
        db.commit()


def test_manager_hierarchy_read_scope(hierarchy: tuple, client: TestClient) -> None:
    orgs, departments, employees, users, evidence = hierarchy
    for manager, org_index, visible in (
        (0, 0, {0, 1, 2}),
        (1, 0, {1, 2}),
        (2, 0, {3}),
        (3, 1, {0, 1, 2}),
    ):
        auth = headers(users[manager])
        url = f"/api/v1/orgs/{orgs[org_index].id}"
        memberships = client.get("/api/v1/me/memberships", headers=auth).json()
        assert set(memberships[0]["managed_department_ids"]) == {
            str(departments[org_index][i].id) for i in visible
        }
        for params in (
            {},
            {"q": "合成员工"},
            {"q": "合成员工2"},
            *(
                {"department_id": str(dept.id), "q": "合成员工"}
                for dept in departments[org_index]
            ),
        ):
            expected = {
                str(e.id)
                for i, e in enumerate(employees[org_index])
                if i in visible
                and (
                    "department_id" not in params
                    or str(e.department_id) == params["department_id"]
                )
                and ("q" not in params or params["q"] in e.display_name)
            }
            response = client.get(f"{url}/employees", params=params, headers=auth)
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == len(expected)
            assert {e["id"] for e in data["data"]} == expected
        for tenant_index, staff in enumerate(employees):
            for index, employee in enumerate(staff):
                expected_status = (
                    200 if tenant_index == org_index and index in visible else 404
                )
                for suffix in ("", "/profile", "/profile-versions", "/evidence"):
                    response = client.get(
                        f"{url}/employees/{employee.id}{suffix}", headers=auth
                    )
                    assert response.status_code == expected_status
                    if expected_status == 200 and index == 2:
                        if not suffix:
                            assert (
                                response.json()["progress"]["fill_status"]
                                == "not_started"
                            )
                            assert response.json()["latest_published"]["revision"] == 1
                        if suffix == "/evidence":
                            assert response.json()["count"] == 3
                            assert all(
                                not row["redacted"] for row in response.json()["data"]
                            )
            expected_status = 200 if tenant_index == org_index and 2 in visible else 404
            assert (
                client.get(
                    f"{url}/employees/{staff[2].id}/profile-versions/1", headers=auth
                ).status_code
                == expected_status
            )
            for record in evidence[tenant_index]:
                response = client.get(f"{url}/evidence/{record.id}", headers=auth)
                assert response.status_code == expected_status
                if expected_status == 200:
                    assert response.json()["content"] == {"text": "合成正文"}
                    assert response.json()["redacted"] is False
        foreign_url = f"/api/v1/orgs/{orgs[1 - org_index].id}/employees"
        assert client.get(foreign_url, headers=auth).status_code == 404


def test_scope_changes_on_next_request(
    hierarchy: tuple, db: Session, client: TestClient
) -> None:
    orgs, departments, employees, users, evidence = hierarchy
    url = f"/api/v1/orgs/{orgs[0].id}"
    quality = departments[0][2]
    quality.parent_department_id = departments[0][3].id
    db.add(quality)
    db.commit()
    for manager, visible in ((0, {0, 1}), (1, {1}), (2, {2, 3})):
        auth = headers(users[manager])
        result = client.get(f"{url}/employees", headers=auth).json()
        assert result["count"] == len(visible)
        assert {e["id"] for e in result["data"]} == {
            str(employees[0][i].id) for i in visible
        }
        memberships = client.get("/api/v1/me/memberships", headers=auth).json()
        assert set(memberships[0]["managed_department_ids"]) == {
            str(departments[0][i].id) for i in visible
        }
        for suffix in (
            "",
            "/profile",
            "/profile-versions",
            "/profile-versions/1",
            "/evidence",
        ):
            assert client.get(
                f"{url}/employees/{employees[0][2].id}{suffix}", headers=auth
            ).status_code == (200 if manager == 2 else 404)
        for record in evidence[0]:
            response = client.get(f"{url}/evidence/{record.id}", headers=auth)
            assert response.status_code == (200 if manager == 2 else 404)
            if manager == 2:
                assert response.json()["content"] == {"text": "合成正文"}


def test_cycle_and_tenant_boundaries(
    hierarchy: tuple, db: Session, client: TestClient
) -> None:
    orgs, departments, employees, users, _ = hierarchy
    root = departments[0][0]
    root.parent_department_id = departments[0][2].id
    db.add(root)
    db.commit()
    # Bound the regression check so UNION ALL cannot hang the test suite.
    db.execute(text("SET LOCAL statement_timeout = '3s'"))
    try:
        assert managed_scope(
            db, orgs[0].id, frozenset([root.id, departments[1][0].id])
        ) == frozenset(dept.id for dept in departments[0][:3])
    finally:
        db.rollback()
    result = client.get(
        f"/api/v1/orgs/{orgs[0].id}/employees", headers=headers(users[0])
    )
    assert result.status_code == 200
    assert result.json()["count"] == 3
    assert {e["id"] for e in result.json()["data"]} == {
        str(e.id) for e in employees[0][:3]
    }


def test_no_grants_skip_query(db: Session) -> None:
    queries = []

    def record_query(*args: object) -> None:
        queries.append(args)

    event.listen(engine, "before_cursor_execute", record_query)
    try:
        assert managed_scope(db, uuid.uuid7(), frozenset()) == frozenset()
    finally:
        event.remove(engine, "before_cursor_execute", record_query)
    assert queries == []


def test_nonmember_and_missing_org_are_identical(
    hierarchy: tuple, client: TestClient
) -> None:
    orgs, _, _, users, _ = hierarchy
    auth = headers(users[3])
    responses = [
        client.get(f"/api/v1/orgs/{org_id}/employees", headers=auth)
        for org_id in (orgs[0].id, uuid.uuid7())
    ]
    assert all(response.status_code == 404 for response in responses)
    assert (
        responses[0].json()
        == responses[1].json()
        == {"detail": "未找到该企业或你没有权限访问"}
    )
