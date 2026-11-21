import asyncio
from collections.abc import Generator
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.authz.deps import get_org_principal
from app.core.security import create_access_token
from app.domains.admin_assistant.registry import admin_read
from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition, CapabilitySchemaVersion
from app.domains.change_request.models import (
    ChangeItem,
    ChangeOp,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.evidence.models import Evidence, EvidenceKind, Visibility
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.profile.models import AssessmentStatus, ProfileClaim, ProfileVersion
from app.domains.source.models import SourceConnection, SyncRun
from app.models import User
from tests.utils.org import delete_test_organization


@pytest.fixture
def coverage_data(db: Session) -> Generator[tuple]:
    org = Organization(slug=str(uuid4()), name="合成覆盖率企业")
    foreign = Organization(slug=str(uuid4()), name="合成其他企业")
    db.add_all([org, foreign])
    db.flush()
    parent = Department(org_id=org.id, name="研发")
    sibling = Department(org_id=org.id, name="平级")
    db.add_all([parent, sibling])
    db.flush()
    child = Department(org_id=org.id, name="研发子部门", parent_department_id=parent.id)
    empty = Department(org_id=org.id, name="空部门", parent_department_id=parent.id)
    db.add_all([child, empty])
    db.flush()
    people = {
        key: Employee(org_id=org.id, display_name=key, department_id=department)
        for key, department in [
            ("published", parent.id),
            ("disputed", parent.id),
            ("pending_employee", child.id),
            ("pending_manager", child.id),
            ("none", child.id),
            ("sibling", sibling.id),
            ("unassigned", None),
            ("inactive", child.id),
        ]
    }
    people["inactive"].employment_status = EmploymentStatus.INACTIVE
    db.add_all(people.values())
    db.flush()
    users, principals, headers = {}, {}, {}
    for role in ("admin", "manager", "employee", "foreign"):
        user = User(
            username=str(uuid4()),
            email=f"{uuid4()}@example.com",
            hashed_password="unused",
        )
        db.add(user)
        db.flush()
        member = OrgMembership(
            org_id=foreign.id if role == "foreign" else org.id,
            user_id=user.id,
            employee_id=people["published" if role == "manager" else "none"].id
            if role in ("manager", "employee")
            else None,
        )
        db.add(member)
        db.flush()
        if role != "employee":
            db.add(
                RoleGrant(
                    org_id=member.org_id,
                    membership_id=member.id,
                    role=Role.DEPT_MANAGER if role == "manager" else Role.ORG_ADMIN,
                    department_id=parent.id if role == "manager" else None,
                    granted_by_user_id=user.id,
                )
            )
        users[role] = user
        headers[role] = {
            "Authorization": f"Bearer {create_access_token(user.id, timedelta(minutes=10))}"
        }
    schema = CapabilitySchemaVersion(org_id=org.id, version=1)
    db.add(schema)
    db.flush()
    capability = CapabilityDefinition(
        org_id=org.id, schema_version_id=schema.id, key="test", name="合成能力"
    )
    db.add(capability)
    db.flush()
    db.add(
        ProfileVersion(
            org_id=org.id,
            subject_employee_id=people["published"].id,
            revision=1,
            schema_version_id=schema.id,
            snapshot={},
            published_at=utc_now(),
            published_by={},
        )
    )
    db.add(
        ProfileClaim(
            org_id=org.id,
            subject_employee_id=people["disputed"].id,
            capability_definition_id=capability.id,
            statement="PRIVATE_STATEMENT",
            explanation="PRIVATE_EXPLANATION",
            assessment_status=AssessmentStatus.DISPUTED,
            generated_by={},
        )
    )
    db.add(
        Evidence(
            org_id=org.id,
            subject_employee_id=people["disputed"].id,
            kind=EvidenceKind.SELF_REPORT,
            source="self",
            source_ref="test",
            title="PRIVATE_TITLE",
            content={"text": "PRIVATE_BODY"},
            visibility=Visibility.RESTRICTED,
            provenance={},
            content_hash="synthetic",
        )
    )
    old = utc_now() - timedelta(days=30)
    requests = {}
    for key, status in [
        ("pending_employee", RequestStatus.PENDING_OWNER),
        ("pending_manager", RequestStatus.PENDING_REVIEW),
        ("sibling", RequestStatus.PENDING_REVIEW),
        ("inactive", RequestStatus.PENDING_REVIEW),
    ]:
        request = ProfileChangeRequest(
            org_id=org.id,
            subject_employee_id=people[key].id,
            origin=RequestOrigin.INITIAL_SYNC
            if key == "pending_employee"
            else RequestOrigin.MANUAL,
            status=status,
            author={},
            created_at=old,
            submitted_at=old,
        )
        db.add(request)
        db.flush()
        db.add(
            ChangeItem(
                org_id=org.id,
                request_id=request.id,
                position=0,
                op=ChangeOp.ADD,
                capability_definition_id=capability.id,
                after={"statement": "PRIVATE_STATEMENT"},
            )
        )
        requests[key] = request
    connection = SourceConnection(
        org_id=org.id,
        kind="jira",
        config={"deployment": "cloud", "username": "PRIVATE_USERNAME"},
        secret_ciphertext="PRIVATE_SECRET",
        updated_by_user_id=users["admin"].id,
    )
    db.add(connection)
    db.flush()
    run = SyncRun(
        org_id=org.id,
        connection_id=connection.id,
        status="failed",
        requested_by_user_id=users["admin"].id,
        error="PRIVATE_ERROR",
        finished_at=old,
    )
    db.add(run)
    db.commit()
    for role, user in users.items():
        principals[role] = get_org_principal(
            foreign.id if role == "foreign" else org.id, user, db
        )
    try:
        yield org, people, headers, principals, requests, run
    finally:
        db.rollback()
        delete_test_organization(db, org.id)
        delete_test_organization(db, foreign.id)
        for user in users.values():
            db.delete(user)
        db.commit()


def read(client: TestClient, data: tuple, role: str) -> dict:
    response = client.get(f"/api/v1/orgs/{data[0].id}/coverage", headers=data[2][role])
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == {
        "total",
        "statuses",
        "departments",
        "overdue_initial_requests",
        "review_backlog_requests",
        "jira_sync_failure",
    }
    for summary in [result, *result["departments"]]:
        for status in summary["statuses"]:
            assert set(status) == {"status", "count", "percentage"}
    for department in result["departments"]:
        assert set(department) == {
            "department_id",
            "department_name",
            "total",
            "statuses",
            "with_descendants",
        }
    if result["jira_sync_failure"]:
        assert set(result["jira_sync_failure"]) == {"connection_name", "failed_at"}
    assert "PRIVATE_" not in str(result)
    return result


def test_coverage_scope_and_statuses(client: TestClient, coverage_data: tuple) -> None:
    data = coverage_data
    manager = read(client, data, "manager")
    assert manager["total"] == 5
    assert {
        s["status"]: (s["count"], s["percentage"]) for s in manager["statuses"]
    } == dict.fromkeys(
        ("none", "published", "disputed", "pending_employee", "pending_manager"),
        (1, 20.0),
    )
    assert {d["department_name"]: d["total"] for d in manager["departments"]} == {
        "研发": 2,
        "研发子部门": 3,
        "空部门": 0,
    }
    assert all(s["percentage"] == 0 for s in manager["departments"][-1]["statuses"])
    assert (
        manager["overdue_initial_requests"] == manager["review_backlog_requests"] == 1
    )
    assert manager["jira_sync_failure"] is None
    admin = read(client, data, "admin")
    assert admin["total"] == 7
    assert {s["status"]: s["count"] for s in admin["statuses"]} == {
        "none": 2,
        "published": 1,
        "disputed": 1,
        "pending_employee": 1,
        "pending_manager": 2,
    }
    assert admin["statuses"][0]["percentage"] == 28.6
    assert admin["overdue_initial_requests"] == 1
    assert admin["review_backlog_requests"] == 2
    assert admin["jira_sync_failure"] == {
        "connection_name": "Jira Cloud",
        "failed_at": data[5].finished_at.isoformat().replace("+00:00", "Z"),
    }


@pytest.mark.parametrize(
    ("role", "endpoint"), [("manager", "employees"), ("admin", "accounts")]
)
def test_coverage_filters_people_before_pagination(
    client: TestClient, coverage_data: tuple, role: str, endpoint: str
) -> None:
    org, people, headers, *_ = coverage_data
    url = f"/api/v1/orgs/{org.id}/{endpoint}"
    summary = read(client, coverage_data, role)
    for metric in summary["statuses"]:
        status = metric["status"]
        expected = {status}
        if role == "admin":
            expected |= {"sibling"} if status == "pending_manager" else set()
            expected |= {"unassigned"} if status == "none" else set()
        response = client.get(
            url, headers=headers[role], params={"review_status": status}
        )
        assert response.status_code == 200
        page = response.json()
        assert {row["id"] for row in page["data"]} == {
            str(people[key].id) for key in expected
        }
        assert page["count"] == metric["count"]

    params = {
        "review_status": "pending_manager",
        "department_id": str(people["pending_manager"].department_id),
        "q": "pending",
        "limit": 1,
    }
    page = client.get(url, headers=headers[role], params=params).json()
    assert page["count"] == 1
    assert [row["id"] for row in page["data"]] == [str(people["pending_manager"].id)]
    assert client.get(
        url, headers=headers[role], params={**params, "skip": 1}
    ).json() == {"count": 1, "data": []}
    assert client.get(url, headers=headers["foreign"], params=params).status_code == 404
    if endpoint == "accounts":
        assert (
            client.get(url, headers=headers["manager"], params=params).status_code
            == 403
        )


def test_coverage_denies_other_roles_and_tenants(
    client: TestClient, coverage_data: tuple
) -> None:
    data = coverage_data
    for role, expected in [("employee", 403), ("foreign", 404)]:
        response = client.get(
            f"/api/v1/orgs/{data[0].id}/coverage", headers=data[2][role]
        )
        assert response.status_code == expected
        assert "PRIVATE_" not in response.text
    own_foreign = client.get(
        f"/api/v1/orgs/{data[3]['foreign'].org_id}/coverage", headers=data[2]["admin"]
    )
    assert own_foreign.status_code == 404


def test_coverage_admin_tool_matches_api(
    client: TestClient, db: Session, coverage_data: tuple
) -> None:
    data = coverage_data
    result = asyncio.run(admin_read(db, data[3]["admin"], "get_coverage", {}))
    assert result["data"] == read(client, data, "admin")
    assert "PRIVATE_" not in str(result)
    for role in ("manager", "employee"):
        result = asyncio.run(admin_read(db, data[3][role], "get_coverage", {}))
        assert result["data"] == {"error": "需要企业管理员权限"}
    foreign = asyncio.run(admin_read(db, data[3]["foreign"], "get_coverage", {}))[
        "data"
    ]
    assert foreign["total"] == 0 and foreign["departments"] == []
    assert foreign["jira_sync_failure"] is None
    assert (
        foreign["overdue_initial_requests"] == foreign["review_backlog_requests"] == 0
    )
    assert all(s["count"] == s["percentage"] == 0 for s in foreign["statuses"])
    assert (
        "error"
        in asyncio.run(
            admin_read(db, data[3]["admin"], "get_coverage", {"org_id": str(uuid4())})
        )["data"]
    )


def test_coverage_backlog_and_latest_sync(
    client: TestClient, db: Session, coverage_data: tuple
) -> None:
    data = coverage_data
    data[1]["pending_employee"].auto_draft_opt_out = True
    data[4]["pending_manager"].submitted_at = utc_now()
    db.add_all([data[1]["pending_employee"], data[4]["pending_manager"]])
    db.add(
        SyncRun(
            org_id=data[0].id,
            connection_id=data[5].connection_id,
            status="succeeded",
            requested_by_user_id=data[3]["admin"].user_id,
        )
    )
    db.commit()
    manager = read(client, data, "manager")
    assert (
        manager["overdue_initial_requests"] == manager["review_backlog_requests"] == 0
    )
    assert read(client, data, "admin")["jira_sync_failure"] is None
