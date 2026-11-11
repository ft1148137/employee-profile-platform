from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.base import utc_now
from app.domains.org.models import Department, EmploymentStatus
from app.domains.profile.models import ProfileVersion
from tests.api.routes.test_dictionary_versions import exported, save, upload


def test_stats_use_latest_frozen_claims_active_keys_and_employee_scope(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    org_id = employees["E01"].org_id
    dictionary = client.get(f"{url}/capabilities", headers=auth["admin"]).json()
    target, removed, empty = dictionary["data"][:3]
    for version in db.exec(
        select(ProfileVersion).where(ProfileVersion.org_id == org_id)
    ).all():
        version.snapshot = {"claims": []}
        db.add(version)
    child = Department(
        org_id=org_id,
        name="合成下级部门",
        parent_department_id=employees["E01"].department_id,
    )
    db.add(child)
    db.flush()
    employees["E03"].department_id = child.id
    employees["E05"].employment_status = EmploymentStatus.INACTIVE
    db.add_all([employees["E03"], employees["E05"]])

    def claim(capability_id: str, status: str, level: str | None) -> dict:
        return {
            "id": str(uuid4()),
            "capability_definition_id": capability_id,
            "statement": "合成发布判断",
            "level": level,
            "assessment_status": status,
            "confidence": None,
            "explanation": None,
            "generated_by": {"actor_type": "user"},
        }

    def version(code: str, revision: int, claims: list[dict]) -> None:
        db.add(
            ProfileVersion(
                org_id=org_id,
                subject_employee_id=employees[code].id,
                revision=revision,
                schema_version_id=dictionary["schema_version"]["id"],
                snapshot={"claims": claims},
                published_at=utc_now(),
                published_by={"actor_type": "user"},
            )
        )

    version("E01", 10, [claim(target["id"], "tentative", "old")])
    version("E01", 11, [claim(target["id"], "confirmed", "routine")])
    version("E02", 10, [claim(target["id"], "tentative", None)])
    version("E03", 10, [claim(target["id"], "unknown", None)])
    version(
        "E04",
        10,
        [
            claim(target["id"], "disputed", "independent_complex"),
            claim(removed["id"], "confirmed", "routine"),
        ],
    )
    version("E05", 10, [claim(target["id"], "confirmed", "routine")])
    db.commit()
    # Publish a new dictionary: old snapshots must match by key, not definition ID.
    definitions = [
        d
        for d in exported(client, url, auth["admin"])
        if d["key"] in (target["key"], empty["key"])
    ]
    response = save(
        client, url, auth["admin"], upload(client, url, auth["admin"], definitions)
    )
    assert response.status_code == 200
    for role, count, levels, statuses in (
        (
            "admin",
            4,
            {"routine": 1, "independent_complex": 1, "_none": 2},
            {"confirmed": 1, "tentative": 1, "unknown": 1, "disputed": 1},
        ),
        (
            "manager",
            3,
            {"routine": 1, "_none": 2},
            {"confirmed": 1, "tentative": 1, "unknown": 1, "disputed": 0},
        ),
    ):
        response = client.get(f"{url}/capabilities/stats", headers=auth[role])
        assert response.status_code == 200
        assert response.json() == {
            "data": [
                {
                    "capability_key": target["key"],
                    "employee_count": count,
                    "levels": levels,
                    "statuses": statuses,
                },
                {
                    "capability_key": empty["key"],
                    "employee_count": 0,
                    "levels": {},
                    "statuses": dict.fromkeys(statuses, 0),
                },
            ]
        }


@pytest.mark.parametrize("role,status", [("employee", 403), ("foreign", 404)])
def test_stats_permissions(
    shared_demo: tuple, client: TestClient, role: str, status: int
) -> None:
    url, _, auth = shared_demo
    assert (
        client.get(f"{url}/capabilities/stats", headers=auth[role]).status_code
        == status
    )
    assert (
        client.get(f"{url}/capabilities", headers=auth["employee"]).status_code == 200
    )


def test_stats_exclude_other_tenant_profiles(
    shared_demo: tuple, client: TestClient
) -> None:
    url, _, auth = shared_demo
    own = client.get(f"{url}/capabilities/stats", headers=auth["admin"]).json()["data"]
    # The session fixture also has a copy of the same demo in a different tenant.
    assert sum(s["employee_count"] for s in own) == 5
