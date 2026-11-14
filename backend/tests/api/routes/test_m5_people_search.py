from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.base import utc_now
from app.domains.capability.models import DepartmentCapability
from app.domains.change_request.models import ChangeItem, ProfileChangeRequest
from app.domains.evidence.models import Evidence
from app.domains.org.models import Department, Organization
from app.domains.profile.models import ProfileVersion
from app.domains.review.models import ReviewAppeal
from app.models import User


@pytest.fixture
def people(demo: tuple, db: Session, client: TestClient) -> tuple:
    url, employees, auth = demo
    org_id = employees["E01"].org_id
    dictionary = client.get(f"{url}/capabilities", headers=auth["admin"]).json()
    capability = dictionary["data"][0]
    manager = db.exec(
        select(User).where(User.email == "manager.platform@demo.example.com")
    ).one()
    child = Department(
        org_id=org_id,
        name="合成下级",
        parent_department_id=employees["E01"].department_id,
    )
    upper = Department(org_id=org_id, name="合成上级")
    db.add_all([child, upper])
    db.flush()
    department = db.get_one(Department, employees["E01"].department_id)
    department.parent_department_id = upper.id
    employees["E03"].department_id = child.id
    employees["E04"].department_id = upper.id
    employees["E05"].employment_status = "inactive"
    employees["E01"].job_family = "研发"
    employees["E01"].work_location = "上海"
    employees["E01"].hire_date = date(2020, 1, 1)
    db.add_all([department, *employees.values()])
    db.add(
        DepartmentCapability(
            org_id=org_id,
            department_id=department.id,
            capability_key=capability["key"],
            created_by_user_id=manager.id,
        )
    )
    for version in db.exec(
        select(ProfileVersion).where(ProfileVersion.org_id == org_id)
    ).all():
        version.snapshot = {"claims": []}
        db.add(version)
    now = utc_now().replace(microsecond=0)
    for code in ("E01", "E02", "E03", "E04", "E05", "E06"):
        employee = employees[code]
        evidence = []
        for status, occurred in (
            ("active", now - timedelta(days=30)),
            ("retracted", now),
        ):
            record = Evidence(
                org_id=org_id,
                subject_employee_id=employee.id,
                kind="self_report",
                source="synthetic",
                source_ref=str(uuid4()),
                title="SECRET_TITLE",
                content={"text": "SECRET_BODY"},
                provenance={},
                content_hash=str(uuid4()),
                visibility="restricted",
                status=status,
                occurred_at=occurred,
            )
            db.add(record)
            evidence.append(
                {
                    "id": str(record.id),
                    "kind": "self_report",
                    "title": "SECRET_TITLE",
                    "relation": "supports",
                }
            )
        claim = {
            "id": str(uuid4()),
            "capability_definition_id": capability["id"],
            "statement": "合成已发布陈述",
            "level": None
            if code == "E02"
            else "routine"
            if code == "E03"
            else "independent_complex",
            "assessment_status": "tentative" if code == "E02" else "confirmed",
            "confidence": None,
            "explanation": "SECRET_EXPLANATION",
            "generated_by": {"actor_type": "user"},
            "evidence": evidence,
        }
        for revision in (10, 11):
            db.add(
                ProfileVersion(
                    org_id=org_id,
                    subject_employee_id=employee.id,
                    revision=revision,
                    schema_version_id=dictionary["schema_version"]["id"],
                    snapshot={"claims": [claim]},
                    published_at=now - timedelta(days=11 - revision),
                    published_by={"actor_type": "user", "user_id": str(manager.id)}
                    if revision == 10
                    else {"actor_type": "system_worker"},
                )
            )
    request = ProfileChangeRequest(
        org_id=org_id,
        subject_employee_id=employees["E03"].id,
        origin="manual",
        status="approved",
        author={},
    )
    db.add(request)
    db.flush()
    item = ChangeItem(
        org_id=org_id,
        request_id=request.id,
        position=0,
        op="add",
        capability_definition_id=capability["id"],
        after={},
    )
    db.add(item)
    db.flush()
    db.add(
        ReviewAppeal(
            org_id=org_id,
            request_id=request.id,
            item_id=item.id,
            employee_id=employees["E03"].id,
            reason="合成申诉",
        )
    )
    db.commit()
    return url, employees, auth, capability, now, upper


def test_scope_privacy_and_first_publisher(people: tuple, client: TestClient) -> None:
    url, employees, auth, capability, now, upper = people
    params = {"capability_key": capability["key"]}
    for role, expected in (
        ("manager", {"E01", "E02", "E03"}),
        ("admin", {"E01", "E02", "E03", "E04", "E06"}),
    ):
        response = client.get(f"{url}/people-search", params=params, headers=auth[role])
        assert response.status_code == 200
        rows = response.json()["data"]
        assert {r["employee_code"] for r in rows} == expected
        assert response.json()["count"] == len(expected)
        for row in rows:
            assert set(row) == {
                "employee_id",
                "display_name",
                "employee_code",
                "department_name",
                "capability_key",
                "capability_name",
                "statement",
                "level",
                "level_name",
                "status",
                "evidence_count",
                "latest_evidence_at",
                "published_by",
                "published_at",
            }
            assert set(row["published_by"]) == {"actor_type", "display_name"}
            assert row["published_by"]["display_name"] == employees["E01"].display_name
            assert row["published_at"] == (now - timedelta(days=1)).isoformat().replace(
                "+00:00", "Z"
            )
            assert row["evidence_count"] == 1
            assert row["latest_evidence_at"] == (
                now - timedelta(days=30)
            ).isoformat().replace("+00:00", "Z")
        assert "SECRET" not in response.text
    assert (
        client.get(
            f"{url}/people-search",
            params={**params, "department_id": str(upper.id)},
            headers=auth["manager"],
        ).status_code
        == 403
    )
    sibling = employees["E06"].department_id
    assert (
        client.get(
            f"{url}/people-search",
            params={**params, "department_id": str(sibling)},
            headers=auth["manager"],
        ).status_code
        == 403
    )
    for role, status in (("employee", 403), ("foreign", 404)):
        assert (
            client.get(
                f"{url}/people-search", params=params, headers=auth[role]
            ).status_code
            == status
        )


def test_filters_and_pagination(people: tuple, client: TestClient, db: Session) -> None:
    url, employees, auth, capability, now, _ = people

    def search(**filters: object) -> dict:
        response = client.get(
            f"{url}/people-search",
            params={"capability_key": capability["key"], **filters},
            headers=auth["manager"],
        )
        assert response.status_code == 200, response.text
        return response.json()

    assert (
        search(
            minimum_level="independent_complex",
            evidence_from=(now - timedelta(days=365)).date().isoformat(),
        )["count"]
        == 1
    )
    assert (
        search(minimum_level="independent_complex", include_ungraded=True)["count"] == 2
    )
    assert search(statuses=["disputed"])["data"][0]["employee_code"] == "E03"
    assert search(statuses=["tentative", "disputed"])["count"] == 2
    assert search(department_id=str(employees["E01"].department_id))["count"] == 3
    assert search(department_capability_only=True)["count"] == 3
    assert (
        search(evidence_to=(now - timedelta(days=31)).date().isoformat())["count"] == 0
    )
    assert (
        search(evidence_from=(now - timedelta(days=29)).date().isoformat())["count"]
        == 0
    )
    assert (
        search(
            job_family="不存在", work_location="不存在", hire_date_from="2099-01-01"
        )["count"]
        == 3
    )
    org = db.get_one(Organization, employees["E01"].org_id)
    org.employee_fields = ["job_family", "work_location", "hire_date"]
    db.add(org)
    db.commit()
    assert (
        search(
            job_family="研发",
            work_location="上海",
            hire_date_from="2020-01-01",
            hire_date_to="2020-01-01",
        )["count"]
        == 1
    )
    assert search(job_family="不存在")["count"] == 0
    assert search(work_location="不存在")["count"] == 0
    assert search(hire_date_to="2019-12-31")["count"] == 0
    assert [r["employee_code"] for r in search()["data"]] == ["E01", "E03", "E02"]
    assert search(skip=1, limit=1)["data"][0]["employee_code"] == "E03"
    assert search(skip=9)["data"] == []
    for filters in (
        {"minimum_level": "invalid"},
        {"limit": 101},
        {"statuses": "unknown"},
        {"evidence_from": "2026-02-02", "evidence_to": "2026-02-01"},
    ):
        assert (
            client.get(
                f"{url}/people-search",
                params={"capability_key": capability["key"], **filters},
                headers=auth["manager"],
            ).status_code
            == 422
        )
