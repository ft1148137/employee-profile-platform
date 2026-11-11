import asyncio
import json
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session
from starlette.routing import Route

from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import registry
from app.domains.bot import store, tools
from app.domains.org.employee_fields import FIELD_LABELS
from app.domains.org.models import Organization
from app.domains.org.roster.service import RosterError
from app.domains.profile import service as profiles
from app.mcp.server import endpoint, lifespan
from tests.admin_assistant.test_personnel import principal, propose
from tests.admin_assistant.test_personnel import roster as roster
from tests.api.routes.test_employee_fields import switches
from tests.api.routes.test_employee_requests import principal_for
from tests.api.routes.test_mcp import call, initialize, token_for
from tests.api.routes.test_roster import apply, employees
from tests.api.routes.test_roster import client as client
from tests.api.routes.test_setup_steps import SetupTenant
from tests.api.routes.test_setup_steps import tenant as tenant
from tests.authz.test_tenant_access import headers
from tests.authz.test_tenant_access import tenant_data as tenant_data

VALUES = {"job_family": "技术", "work_location": "上海", "hire_date": "2020-03-04"}


@pytest.mark.parametrize("via", ["web", "assistant"])
@pytest.mark.parametrize("kind", ["onboard", "transfer"])
def test_write_preserve_and_clear(
    db: Session,
    tenant: SetupTenant,
    client: TestClient,
    roster: dict,
    via: str,
    kind: str,
) -> None:
    switches(client, tenant, list(FIELD_LABELS))
    other = roster["C"]
    other.job_family, other.work_location, other.hire_date = (
        "运营",
        "北京",
        date(2018, 1, 1),
    )
    db.add(other)
    db.commit()

    def change(body: dict) -> None:
        if via == "web":
            response = client.post(f"{tenant.url}/roster/single-change", json=body)
            assert response.status_code == 200, response.text
            assert apply(client, tenant, response.json()).status_code == 200
        else:
            row = propose(db, tenant, body)
            for field, value in body.items():
                if field in FIELD_LABELS:
                    assert row.items[0]["after"][field] == value
                    assert (value or "清空") in row.items[0]["impact"]["text"]
            result = c.apply(db, principal(db, tenant), row.id, row.revision)
            assert result.status == "applied" and not result.items[0].undoable
        db.expire_all()
        saved = employees(db, tenant)["C"]
        assert (saved.job_family, saved.work_location, saved.hire_date) == (
            "运营",
            "北京",
            date(2018, 1, 1),
        )

    body = (
        {
            "kind": "onboard",
            "employee_code": "D",
            "display_name": "丁",
            "email": "d@example.com",
            "department": "研发",
        }
        if kind == "onboard"
        else {"kind": "transfer", "employee_id": str(roster["B"].id)}
    )
    change({**body, **VALUES})
    target = employees(db, tenant)["D" if kind == "onboard" else "B"]
    transfer = {"kind": "transfer", "employee_id": str(target.id)}
    change({**transfer, "title": "新职位"})
    db.refresh(target)
    assert (target.job_family, target.work_location, target.hire_date) == (
        "技术",
        "上海",
        date(2020, 3, 4),
    )
    change({**transfer, **dict.fromkeys(VALUES)})
    db.refresh(target)
    assert target.job_family is target.work_location is target.hire_date is None


@pytest.mark.parametrize("field", list(FIELD_LABELS))
@pytest.mark.parametrize("kind", ["onboard", "transfer"])
def test_disabled_rejected_before_proposal(
    db: Session,
    tenant: SetupTenant,
    client: TestClient,
    roster: dict,
    field: str,
    kind: str,
) -> None:
    body = (
        {
            "kind": "onboard",
            "employee_code": "D",
            "display_name": "丁",
            "email": "d@example.com",
            "department": "研发",
        }
        if kind == "onboard"
        else {"kind": "transfer", "employee_id": str(roster["B"].id)}
    )
    for value in (VALUES[field], None):
        body[field] = value
        response = client.post(f"{tenant.url}/roster/single-change", json=body)
        assert response.status_code == 422
        assert response.json()["detail"] == f"「{FIELD_LABELS[field]}」未开启"
        with pytest.raises(RosterError) as error:
            propose(db, tenant, body)
        assert error.value.status_code == 422
    assert "D" not in employees(db, tenant)
    db.refresh(roster["B"])
    assert getattr(roster["B"], field) is None


def test_fields_preview_becomes_stale(
    db: Session, tenant: SetupTenant, client: TestClient, roster: dict
) -> None:
    switches(client, tenant, list(FIELD_LABELS))
    body = {"kind": "transfer", "employee_id": str(roster["B"].id), **VALUES}
    preview = client.post(f"{tenant.url}/roster/single-change", json=body).json()
    row = propose(db, tenant, body)
    switches(client, tenant, [])
    assert apply(client, tenant, preview).status_code == 409
    with pytest.raises(c.ChangeSetError) as error:
        c.apply(db, principal(db, tenant), row.id, row.revision)
    assert error.value.status_code == 409
    db.refresh(roster["B"])
    assert roster["B"].hire_date is None


def test_employee_field_read_scope(
    db: Session, client: TestClient, tenant_data: tuple
) -> None:
    orgs, _, people, users = tenant_data
    orgs[0].employee_fields = list(FIELD_LABELS)
    db.add(orgs[0])
    for person in people:
        person.job_family, person.work_location, person.hire_date = (
            "技术",
            "上海",
            date(2020, 3, 4),
        )
        db.add(person)
    db.commit()
    url = f"/api/v1/orgs/{orgs[0].id}/employees"
    for user_index, visible in (
        (0, {0, 1, 2, 3, 4}),
        (1, {0, 1, 3, 4}),
        (2, {2}),
        (3, set()),
    ):
        auth = headers(users[user_index])
        listing = client.get(url, headers=auth)
        if visible:
            assert listing.status_code == 200
            assert {p["id"] for p in listing.json()["data"]} == {
                str(people[i].id) for i in visible
            }
            assert all(
                {f: p[f] for f in VALUES} == VALUES for p in listing.json()["data"]
            )
        else:
            assert listing.status_code == 404
        for i, person in enumerate(people):
            for suffix in ("", "/profile"):
                response = client.get(f"{url}/{person.id}{suffix}", headers=auth)
                assert response.status_code == (200 if i in visible else 404)
                if i in visible:
                    data = response.json()["employee"] if suffix else response.json()
                    assert {f: data[f] for f in VALUES} == VALUES
    orgs[0].employee_fields = []
    db.add(orgs[0])
    db.commit()
    for path in ("", f"/{people[0].id}", f"/{people[0].id}/profile"):
        assert not any(
            f in client.get(url + path, headers=headers(users[0])).text for f in VALUES
        )
    db.refresh(people[0])
    assert people[0].hire_date == date(2020, 3, 4)


def test_employee_tool_outputs_exclude_fields(db: Session, demo: tuple) -> None:
    _, people, _ = demo
    person = people["E02"]
    org = db.get_one(Organization, person.org_id)
    org.employee_fields = list(FIELD_LABELS)
    person.job_family, person.work_location, person.hire_date = (
        "技术",
        "上海",
        date(2020, 3, 4),
    )
    db.add_all([org, person])
    db.commit()
    p = principal_for(db, person.org_id)
    assert profiles.read_profile(db, p, person.id).employee.hire_date == date(
        2020, 3, 4
    )
    versions = profiles.list_versions(db, p, person.id)
    assert versions.data
    for version in versions.data:
        exported = profiles.read_version(db, p, person.id, version.revision).model_dump(
            mode="json"
        )
        assert not any(f in json.dumps(exported) for f in VALUES)
    result = tools.call(db, p, store.create_session(db, p), [], "get_my_profile", {})
    assert not any(f in json.dumps(result) for f in VALUES)
    from tests.admin_assistant.test_registry import principal as admin_principal

    admin_result = asyncio.run(
        registry.admin_read(
            db,
            admin_principal(db, demo),
            "get_employee_profile",
            {"employee_id": str(person.id)},
        )
    )
    assert admin_result["data"]["employee"]["id"] == str(person.id)
    assert not any(f in json.dumps(admin_result) for f in VALUES)
    auth, _ = token_for(db, person)
    app = FastAPI(lifespan=lifespan)
    app.router.routes.append(Route("/mcp", endpoint, methods=["POST", "GET", "DELETE"]))
    with TestClient(app) as mcp:
        result = call(mcp, initialize(mcp, auth), "get_my_profile", {})
        assert not result.get("isError"), result
        assert result["structuredContent"]["employee"]["id"] == str(person.id)
        assert not any(f in json.dumps(result) for f in VALUES)
