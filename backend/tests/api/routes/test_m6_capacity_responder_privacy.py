import json
from collections.abc import Generator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.core.security import create_access_token
from app.domains.capacity import assistant
from app.domains.capacity.models import (
    CapacityDemand,
    CapacityDemandEvent,
    CapacitySetting,
)
from app.domains.org.models import OrgMembership, RoleGrant
from app.models import User
from tests.api.routes.test_mcp import call, initialize, token_for


@pytest.fixture
def privacy_case(demo: tuple, db: Session) -> Generator[tuple]:
    yield demo
    db.rollback()
    org_id = demo[1]["E02"].org_id
    for model in (CapacityDemandEvent, CapacityDemand, CapacitySetting):
        db.execute(delete(model).where(model.org_id == org_id))
    db.commit()


def test_responder_name_is_only_visible_to_target_representatives(
    privacy_case: tuple, db: Session, client: TestClient
) -> None:
    url, people, auth = privacy_case
    org_id = people["E02"].org_id
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.employee_id == people["E05"].id,
        )
    ).one()
    db.add(
        RoleGrant(
            org_id=org_id,
            membership_id=member.id,
            role="dept_manager",
            department_id=people["E05"].department_id,
        )
    )
    db.add(
        CapacitySetting(org_id=org_id, enabled=True, updated_by_user_id=member.user_id)
    )
    db.commit()
    auth["b"] = {
        "Authorization": f"Bearer {create_access_token(member.user_id, expires_delta=timedelta(minutes=10))}"
    }
    base = f"{url}/capacity/demands"
    capability = client.get(f"{url}/capabilities", headers=auth["admin"]).json()[
        "data"
    ][0]
    created = client.post(
        base,
        headers=auth["employee"],
        json={
            "requesting_department_id": str(people["E02"].department_id),
            "target_department_id": str(people["E05"].department_id),
            "capability_key": capability["key"],
            "headcount": 1,
            "allocation_percent": 50,
            "window_start": "2026-12-01",
            "window_end": "2026-12-31",
            "description": "合成隐私回归需求",
        },
    )
    assert created.status_code == 200, created.text
    demand_id = created.json()["id"]
    path = f"{base}/{demand_id}"
    replied = client.post(
        f"{path}/reply",
        headers=auth["b"],
        json={
            "outcome": "can_do",
            "start_date": "2026-12-01",
            "end_date": "2026-12-31",
            "text": "合成部门答复",
            "revision": 1,
        },
    )
    assert replied.status_code == 200, replied.text
    responder = people["E05"].display_name
    assert replied.json()["events"][-1]["responder_name"] == responder
    for role, code in (("employee", "E02"), ("manager", "E01"), ("b", "E05")):
        side = "incoming" if role == "b" else "outgoing"
        mcp_headers = initialize(client, token_for(db, people[code])[0])
        membership = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == org_id,
                OrgMembership.employee_id == people[code].id,
            )
        ).one()
        principal = get_org_principal(org_id, db.get_one(User, membership.user_id), db)
        for name, args, endpoint in (
            ("get_demand", {"demand_id": demand_id}, path),
            ("list_demands", {"side": side}, f"{base}?side={side}"),
        ):
            web = client.get(endpoint, headers=auth[role])
            assert web.status_code == 200, web.text
            mcp = call(client, mcp_headers, name, args)
            assert not mcp.get("isError"), mcp
            bot = assistant.call(db, principal, [], name, args)
            for result in (web.json(), mcp["structuredContent"], bot):
                row = result["data"][0] if name == "list_demands" else result
                expected = responder if role == "b" else None
                assert row["events"][-1]["responder_name"] == expected
            if role != "b":
                for result in (web.json(), mcp, bot):
                    assert responder not in json.dumps(result, ensure_ascii=False)
    accepted = client.post(
        f"{path}/respond",
        headers=auth["employee"],
        json={"action": "accept", "revision": 2},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["events"][1]["responder_name"] is None
    assert responder not in accepted.text
    assert client.get(path, headers=auth["foreign"]).status_code == 404
