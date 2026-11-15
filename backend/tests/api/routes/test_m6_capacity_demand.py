import json
from collections.abc import Callable, Generator
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import delete
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.security import create_access_token
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.capability.search import PeopleSearchInput, find_people
from app.domains.capacity.models import (
    CapacityDemand,
    CapacityDemandEvent,
    CapacitySetting,
    CapacityWorkload,
)
from app.domains.capacity.tools import CAPACITY_TOOLS
from app.domains.inbox.models import InboxItem
from app.domains.org.models import Department, OrgMembership, RoleGrant
from app.domains.profile.models import ProfileVersion


@pytest.fixture
def demand(demo: tuple, db: Session, client: TestClient) -> Generator[tuple]:
    url, employees, auth = demo
    org_id = employees["E01"].org_id
    members = {
        m.employee_id: m
        for m in db.exec(
            select(OrgMembership).where(OrgMembership.org_id == org_id)
        ).all()
    }
    admin = members[None].user_id
    b = members[employees["E05"].id]
    auth["b"] = {
        "Authorization": f"Bearer {create_access_token(b.user_id, expires_delta=timedelta(minutes=10))}"
    }
    target = employees["E04"].department_id
    child = Department(org_id=org_id, name="合成子部门", parent_department_id=target)
    db.add(child)
    db.flush()
    dictionary = client.get(f"{url}/capabilities", headers=auth["admin"]).json()
    capability = dictionary["data"][0]
    for i, code in enumerate(("E04", "E05", "E06", "E07", "E08", "E09")):
        e = employees[code]
        e.department_id = child.id if i % 2 else target
        e.employment_status = "inactive" if code == "E09" else "active"
        db.add(e)
        db.add(
            ProfileVersion(
                org_id=org_id,
                subject_employee_id=e.id,
                revision=99,
                published_at=utc_now(),
                schema_version_id=dictionary["schema_version"]["id"],
                published_by={"actor_type": "system_worker"},
                snapshot={
                    "claims": [
                        {
                            "id": str(uuid4()),
                            "capability_definition_id": capability["id"],
                            "statement": "PRIVATE_STATEMENT",
                            "level": None if code == "E08" else "routine",
                            "assessment_status": "confirmed",
                            "confidence": None,
                            "explanation": None,
                            "generated_by": {"actor_type": "user"},
                            "evidence": [],
                        }
                    ]
                },
            )
        )
        if i < 3:
            db.add(
                CapacityWorkload(
                    org_id=org_id,
                    employee_id=e.id,
                    base_percent=[79, 80, 101][i],
                    updated_by_user_id=admin,
                )
            )
    db.add(
        RoleGrant(
            org_id=org_id, membership_id=b.id, role="dept_manager", department_id=target
        )
    )
    db.add(CapacitySetting(org_id=org_id, enabled=True, updated_by_user_id=admin))
    db.commit()
    body = {
        "requesting_department_id": str(employees["E01"].department_id),
        "target_department_id": str(target),
        "capability_key": capability["key"],
        "minimum_level": "routine",
        "headcount": 2,
        "allocation_percent": 50,
        "window_start": "2026-12-01",
        "window_end": "2026-12-31",
        "description": "PRIVATE_DEMAND",
    }
    yield (url, employees, auth, body, b.user_id)
    db.rollback()
    for model in (
        CapacityDemandEvent,
        CapacityDemand,
        CapacityWorkload,
        CapacitySetting,
    ):
        db.execute(delete(model).where(model.org_id == org_id))
    db.commit()


@pytest.fixture
def api(demand: tuple, client: TestClient) -> Callable[..., Response]:
    def request(
        path: str,
        role: str = "employee",
        method: str = "GET",
        code: int = 200,
        **kwargs: Any,
    ) -> Response:
        path = path if path.startswith("/") else f"/capacity/{path}"
        result = client.request(
            method, f"{demand[0]}{path}", headers=demand[2][role], **kwargs
        )
        assert result.status_code == code, result.text
        return result

    return request


def test_reference_privacy_submission_and_profile_invariance(
    demand: tuple, client: TestClient, db: Session, api: Callable[..., Response]
) -> None:
    url, employees, auth, body, responder = demand
    params = {
        k: body[k] for k in ("target_department_id", "capability_key", "minimum_level")
    }
    profiles = [
        f"{url}/employees/{employees[c].id}/profile"
        for c in ("E04", "E05", "E06", "E07", "E08")
    ]
    reads = [
        *[(p, {}) for p in profiles],
        (f"{url}/people-search", {"capability_key": body["capability_key"]}),
        (f"{url}/profile-export", {"format": "json"}),
    ]
    before = [client.get(p, params=q, headers=auth["b"]) for p, q in reads]
    assert all(r.status_code == 200 for r in before)
    reference = api("reference", params=params)
    assert reference.status_code == 200, reference.text
    assert reference.json() == {
        "department_name": "算法与模型部",
        "capability_name": before[-2].json()["data"][0]["capability_name"],
        "count": 4,
        "available": 1,
        "full": 1,
        "overloaded": 1,
        "unconfirmed": 1,
    }
    assert (
        api(
            "reference", params={**params, "minimum_level": "independent_complex"}
        ).json()["count"]
        == 0
    )
    assert (
        api(
            "reference",
            params={k: v for k, v in params.items() if k != "minimum_level"},
        ).json()["count"]
        == 4
    )
    response = api("demands", json=body, method="POST")
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["revision"] == 1 and row["status"] == "submitted"
    assert [e["kind"] for e in row["events"]] == ["submitted"]
    responses = [reference, response, api("departments")]
    for role, side in (
        ("employee", "outgoing"),
        ("manager", "outgoing"),
        ("b", "incoming"),
    ):
        listing = api("demands", params={"side": side}, role=role)
        assert listing.json()["data"] == [row]
        assert listing.json()["count"] == 1
        responses.append(listing)
    responses.append(api(f"demands/{row['id']}"))
    for result in responses:
        for code in ("E04", "E05", "E06", "E07", "E08", "E09"):
            e = employees[code]
            for private in (
                str(e.id),
                e.display_name,
                e.employee_code,
                "PRIVATE_STATEMENT",
            ):
                assert private not in json.dumps(result.json(), ensure_ascii=False)
    assert [r.json() for r in before] == [
        client.get(p, params=q, headers=auth["b"]).json() for p, q in reads
    ]
    org_id = employees["E01"].org_id
    notices = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == org_id, InboxItem.kind == "capacity"
        )
    ).all()
    assert {n.recipient_user_id for n in notices} == {responder}
    event = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org_id,
            AuditEvent.action == "capacity.demand_submitted",
        )
    ).one()
    assert event.details == {"via": "web", "client_name": None}
    assert "PRIVATE_DEMAND" not in str(event.details)


@pytest.mark.parametrize(
    "route,change",
    [
        ("demands", {"capability_key": "absent"}),
        ("demands", {"minimum_level": "absent"}),
        ("demands", {"window_end": "2025-01-01"}),
        ("demands", {"headcount": 0}),
        ("demands", {"description": " "}),
        ("demands", {"actor_type": "system_worker"}),
        ("reference", {"capability_key": "absent"}),
        ("reference", {"minimum_level": "absent"}),
        ("reference", {"principal": "system_worker"}),
    ],
)
def test_invalid_input(
    demand: tuple, route: str, change: dict, api: Callable[..., Response]
) -> None:
    url, _, auth, body, _ = demand
    if route == "demands":
        response = api("demands", json={**body, **change}, method="POST", code=422)
    else:
        params = {k: body[k] for k in ("target_department_id", "capability_key")}
        response = api("reference", params={**params, **change}, code=422)
    assert response.status_code == 422, response.text


def test_scope_policy_and_worker_boundary(
    demand: tuple, db: Session, api: Callable[..., Response]
) -> None:
    url, employees, auth, body, _ = demand
    params = {k: body[k] for k in ("target_department_id", "capability_key")}
    for role, patch, status in (
        (
            "manager",
            {
                "requesting_department_id": body["target_department_id"],
                "target_department_id": str(employees["E10"].department_id),
            },
            403,
        ),
        ("employee", {"requesting_department_id": body["target_department_id"]}, 422),
        (
            "employee",
            {"requesting_department_id": str(employees["E10"].department_id)},
            403,
        ),
    ):
        api("demands", json={**body, **patch}, role=role, method="POST", code=status)
    for path in ("reference", "demands", "departments"):
        api(
            f"{path}",
            params=params if path == "reference" else {},
            role="foreign",
            code=404,
        )
    api("demands", json=body, role="foreign", method="POST", code=404)
    api(
        "demands",
        json={**body, "target_department_id": str(uuid4())},
        method="POST",
        code=404,
    )
    created = api("demands", json=body, method="POST").json()
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == employees["E01"].org_id,
            OrgMembership.employee_id == employees["E01"].id,
        )
    ).one()
    grant = db.exec(
        select(RoleGrant).where(
            RoleGrant.org_id == member.org_id, RoleGrant.membership_id == member.id
        )
    ).one()
    db.delete(grant)
    db.commit()
    api(f"demands/{created['id']}", role="manager", code=404)
    assert api("demands", role="manager").json()["data"] == []
    api(f"demands/{created['id']}", role="foreign", code=404)
    for role, expected in (("employee", 403), ("admin", 200), ("b", 200)):
        api(
            "/capacity-settings",
            json={"enabled": True, "requester_policy": "managers"},
            role="admin",
            method="PUT",
        )
        api("reference", params=params, role=role, code=expected)
    api("demands", json=body, method="POST", code=403)
    worker = Principal.system_worker(member.org_id)
    assert (
        find_people(
            db,
            worker,
            PeopleSearchInput(
                capability_key=body["capability_key"],
                department_id=UUID(body["target_department_id"]),
            ),
        ).count
        >= 4
    )
    api(
        "/people-search",
        params={
            "capability_key": body["capability_key"],
            "actor_type": "system_worker",
        },
        code=422,
    )
    api("/capacity-settings", json={"enabled": False}, role="admin", method="PUT")
    api(f"demands/{created['id']}", role="admin", code=404)


@pytest.mark.parametrize("ancestor", [True, False])
def test_notification_fallback(
    demand: tuple, db: Session, ancestor: bool, api: Callable[..., Response]
) -> None:
    url, employees, auth, body, responder = demand
    org_id = employees["E01"].org_id
    b = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id, OrgMembership.user_id == responder
        )
    ).one()
    db.execute(
        delete(RoleGrant).where(
            RoleGrant.org_id == org_id, RoleGrant.membership_id == b.id
        )
    )
    target = db.get_one(Department, UUID(body["target_department_id"]))
    target.parent_department_id = employees["E01"].department_id if ancestor else None
    db.add(target)
    db.commit()
    response = api("demands", json=body, method="POST")
    assert response.status_code == 200, response.text
    expected = set(
        db.exec(
            select(OrgMembership.user_id)
            .join(RoleGrant, RoleGrant.membership_id == OrgMembership.id)
            .where(
                OrgMembership.org_id == org_id,
                RoleGrant.org_id == org_id,
                RoleGrant.role == ("dept_manager" if ancestor else "org_admin"),
            )
        ).all()
    )
    notices = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == org_id, InboxItem.kind == "capacity"
        )
    ).all()
    assert {n.recipient_user_id for n in notices} == expected


def test_tool_inputs_are_user_scoped() -> None:
    names = {
        "list_capacity_departments",
        "capacity_reference",
        "submit_demand",
        "list_demands",
        "get_demand",
    }
    tools = [t for t in CAPACITY_TOOLS if t.name in names]
    assert len(tools) == 5
    assert {t.name for t in tools if t.writes} == {"submit_demand"}
    for tool in tools:
        schema = tool.input_model.model_json_schema()
        assert schema["additionalProperties"] is False
        assert (
            not {"principal", "actor_type", "via", "client_name", "org_id", "user_id"}
            & schema["properties"].keys()
        )
