import re
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.core.db import engine
from app.core.security import create_access_token
from app.domains.evidence.models import Evidence, EvidenceKind, EvidenceStatus
from app.domains.evidence.service import append_evidence
from app.domains.org.models import Employee, Organization, OrgMembership
from app.domains.profile.models import ProfileClaim, ProfileVersion
from app.models import User
from tests.utils.org import delete_test_organization


@pytest.mark.parametrize("role", ["admin", "manager", "employee", "foreign"])
def test_role_visibility(shared_demo: tuple, client: TestClient, role: str) -> None:
    url, employees, auth = shared_demo
    base = f"{url}/employees/{employees['E02'].id}"
    expected = 404 if role == "foreign" else 200
    for suffix in (
        "profile",
        "evidence",
        "profile-versions",
        "profile-versions/1",
        "profile-versions/2",
    ):
        assert (
            client.get(f"{base}/{suffix}", headers=auth[role]).status_code == expected
        )
    assert client.get(f"{url}/capabilities", headers=auth[role]).status_code == expected
    rows = client.get(f"{base}/evidence", headers=auth["admin"]).json()["data"]
    for row in rows:
        response = client.get(f"{url}/evidence/{row['id']}", headers=auth[role])
        assert response.status_code == expected
        if role == "foreign":
            continue
        detail = response.json()
        redacted = role == "admin" and row["kind"] != "work_record"
        assert detail["redacted"] == redacted
        assert (detail["content"] is None) == redacted
        if redacted:
            assert detail["title"].endswith("（受限）")
            assert set(detail["provenance"]) == {"source", "imported_at"}
        else:
            assert (
                detail["title"] == row["title"]
                if row["kind"] == "work_record"
                else not detail["title"].endswith("（受限）")
            )
        if row["kind"] == "work_record":
            assert detail["task"]["external_key"] and detail["work_event"]["action"]
    if role != "admin":
        denied = employees[
            "E05" if role == "manager" else "E03" if role == "employee" else "E02"
        ]
        for suffix in ("profile", "evidence", "profile-versions", "profile-versions/1"):
            assert (
                client.get(
                    f"{url}/employees/{denied.id}/{suffix}", headers=auth[role]
                ).status_code
                == 404
            )
        rows = client.get(
            f"{url}/employees/{denied.id}/evidence", headers=auth["admin"]
        ).json()["data"]
        assert all(
            client.get(f"{url}/evidence/{e['id']}", headers=auth[role]).status_code
            == 404
            for e in rows
        )


def test_profiles_versions_and_query_bound(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    capabilities = client.get(f"{url}/capabilities", headers=auth["admin"]).json()
    assert "activated_at" in capabilities["schema_version"]
    queries = []

    def count_query(*args: object) -> None:
        queries.append(args)

    for employee in employees.values():
        profile_url = f"{url}/employees/{employee.id}/profile"
        queries.clear()
        event.listen(engine, "before_cursor_execute", count_query)
        try:
            response = client.get(profile_url, headers=auth["admin"])
        finally:
            event.remove(engine, "before_cursor_execute", count_query)
        assert response.status_code == 200
        assert len(queries) <= 12
        profile = response.json()
        assert [i["capability"] for i in profile["items"]] == capabilities["data"]
        assert sum(profile["counts"].values()) == len(capabilities["data"])
        if employee.employee_code == "E12":
            assert all(
                i["status"] == "unknown" and i["claim"] is None
                for i in profile["items"]
            )
        for item in profile["items"]:
            if item["claim"]:
                assert "review_status" not in item["claim"]
            if item["status"] == "disputed":
                assert any(
                    e["relation"] == "contradicts" for e in item["claim"]["evidence"]
                )
    legacy = db.exec(
        select(ProfileVersion).where(
            ProfileVersion.org_id == employees["E02"].org_id,
            ProfileVersion.subject_employee_id == employees["E02"].id,
            ProfileVersion.revision == 1,
        )
    ).one()
    legacy.snapshot = {
        "claims": [
            dict(c, review_status="pending_manager") for c in legacy.snapshot["claims"]
        ]
    }
    db.add(legacy)
    db.commit()
    base = f"{url}/employees/{employees['E02'].id}"
    for role in ("admin", "manager"):
        history = client.get(f"{base}/profile-versions", headers=auth[role])
        assert [v["revision"] for v in history.json()["data"]] == [2, 1]
        assert history.json()["count"] == 2
        assert all(
            v["published_by"] == {"actor_type": "user", "display_name": "企业管理员"}
            for v in history.json()["data"]
        )
        for revision, status in ((1, "tentative"), (2, "confirmed")):
            response = client.get(
                f"{base}/profile-versions/{revision}", headers=auth[role]
            )
            data = response.json()
            assert sum(data["counts"].values()) == len(capabilities["data"])
            assert (
                next(
                    i for i in data["items"] if i["capability"]["key"] == "backend_api"
                )["status"]
                == status
            )
            for item in data["items"]:
                if item["claim"]:
                    assert item["claim"]["created_at"] is None
                    for ref in item["claim"]["evidence"]:
                        assert ref["redacted"] == (
                            role == "admin" and ref["kind"] != "work_record"
                        )
                        assert ref["title"].endswith("（受限）") == ref["redacted"]
            for body in (history.text, response.text):
                assert all(
                    key not in body
                    for key in ("seed_ref", "user_id", "snapshot", "review_status")
                )
                assert re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", body) is None
            if role == "admin":
                private = db.exec(
                    select(Evidence).where(
                        Evidence.org_id == employees["E02"].org_id,
                        Evidence.kind == EvidenceKind.MANAGER_REVIEW,
                    )
                ).all()
                assert all(e.title not in response.text for e in private)
    assert (
        client.get(f"{base}/profile-versions/999", headers=auth["admin"]).status_code
        == 404
    )
    assert (
        client.get(f"{url}/evidence/{uuid4()}", headers=auth["admin"]).status_code
        == 404
    )


@pytest.mark.parametrize("kind", list(EvidenceKind))
@pytest.mark.parametrize(
    "status", [EvidenceStatus.RETRACTED, EvidenceStatus.SUPERSEDED]
)
def test_inactive_and_pagination(
    demo: tuple,
    client: TestClient,
    db: Session,
    kind: EvidenceKind,
    status: EvidenceStatus,
) -> None:
    url, employees, auth = demo
    employee = employees["E02"]
    original = db.exec(
        select(Evidence).where(
            Evidence.org_id == employee.org_id,
            Evidence.subject_employee_id == employee.id,
            Evidence.kind == kind,
        )
    ).first()
    evidence, _ = append_evidence(
        db,
        org_id=employee.org_id,
        subject_employee_id=employee.id,
        kind=original.kind,
        source="synthetic-test",
        source_ref=str(uuid4()),
        title="Retracted synthetic evidence",
        content={},
        visibility=original.visibility,
        provenance={},
    )
    evidence.status = status
    evidence.status_reason = "Synthetic test fixture"
    db.commit()
    base = f"{url}/employees/{employee.id}/evidence"
    active = client.get(base, headers=auth["admin"]).json()
    all_rows = client.get(f"{base}?include_inactive=true", headers=auth["admin"]).json()
    assert all_rows["count"] == active["count"] + 1
    assert str(evidence.id) not in {e["id"] for e in active["data"]}
    assert str(evidence.id) in {e["id"] for e in all_rows["data"]}
    filtered = client.get(
        f"{base}?kind={original.kind}&skip=1&limit=1", headers=auth["admin"]
    ).json()
    expected = [row for row in active["data"] if row["kind"] == kind]
    assert filtered["count"] == len(expected)
    assert filtered["data"] == expected[1:2]
    for query in ("skip=-1", "limit=101", "kind=invalid"):
        assert client.get(f"{base}?{query}", headers=auth["admin"]).status_code == 422


@pytest.mark.parametrize("role", ["admin", "manager", "employee"])
def test_evidence_only_references_current_claims(
    shared_demo: tuple, client: TestClient, db: Session, role: str
) -> None:
    url, employees, auth = shared_demo
    employee = employees["E02"]
    evidence = db.exec(
        select(Evidence).where(
            Evidence.org_id == employee.org_id,
            Evidence.subject_employee_id == employee.id,
            Evidence.source == "self_report",
            Evidence.source_ref == "SR02",
        )
    ).one()
    claims = {
        claim.generated_by["seed_ref"]: str(claim.id)
        for claim in db.exec(
            select(ProfileClaim).where(
                ProfileClaim.org_id == employee.org_id,
                ProfileClaim.subject_employee_id == employee.id,
            )
        ).all()
    }
    response = client.get(f"{url}/evidence/{evidence.id}", headers=auth[role])
    assert response.status_code == 200
    assert response.json()["claims"] == [
        {
            "claim_id": claims["C02v2"],
            "capability_name": "后端 API 开发",
            "relation": "supports",
        }
    ]
    for revision, reference in ((1, "C02"), (2, "C02v2")):
        history = client.get(
            f"{url}/employees/{employee.id}/profile-versions/{revision}",
            headers=auth[role],
        )
        assert history.status_code == 200
        item = next(
            item
            for item in history.json()["items"]
            if item["capability"]["key"] == "backend_api"
        )
        assert item["claim"]["id"] == claims[reference]


@pytest.mark.parametrize("has_schema", [False, True])
def test_new_employee_has_empty_profile(
    demo: tuple, client: TestClient, db: Session, has_schema: bool
) -> None:
    url, employees, auth = demo
    org_id = employees["E02"].org_id if has_schema else uuid4()
    if not has_schema:
        db.add(Organization(id=org_id, slug=str(org_id), name="空画像测试企业"))
        db.flush()
    account = User(username=str(uuid4()), hashed_password="unused")
    employee = Employee(org_id=org_id, employee_code="EMPTY", display_name="新员工")
    db.add_all([account, employee])
    db.flush()
    membership = OrgMembership(
        org_id=org_id, user_id=account.id, employee_id=employee.id
    )
    db.add(membership)
    db.commit()
    try:
        token = create_access_token(account.id, expires_delta=timedelta(minutes=5))
        response = client.get(
            f"/api/v1/orgs/{org_id}/employees/{employee.id}/profile",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        profile = response.json()
        assert profile["employee"]["display_name"] == "新员工"
        assert profile["latest_published"] is None
        assert all(item["claim"] is None for item in profile["items"])
        assert (profile["schema_version"] is not None) == has_schema
        if not has_schema:
            assert profile["items"] == []
            assert sum(profile["counts"].values()) == 0
        # A different tenant cannot use the empty response to discover the employee.
        assert (
            client.get(
                f"/api/v1/orgs/{org_id}/employees/{employee.id}/profile",
                headers=auth["foreign"],
            ).status_code
            == 404
        )
    finally:
        db.rollback()
        if has_schema:
            db.delete(membership)
            db.flush()
            db.delete(employee)
            db.commit()
        else:
            delete_test_organization(db, org_id)
        db.delete(account)
        db.commit()
