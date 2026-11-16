import json
from collections.abc import Generator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.domains.capacity import assistant
from app.domains.capacity.calendar import week_start
from app.domains.capacity.models import CapacityAssignment, CapacityWeek
from app.domains.profile.models import ProfileVersion
from tests.api.routes.test_m6_capacity_demand import demand as demand
from tests.api.routes.test_m62_small_sample import set_matches
from tests.api.routes.test_mcp import call, initialize, token_for
from tests.domains.test_im_gateway import principal


@pytest.fixture(autouse=True)
def cleanup_assignments(demand: tuple, db: Session) -> Generator[None]:
    yield
    db.rollback()
    db.execute(
        delete(CapacityAssignment).where(
            CapacityAssignment.org_id == demand[1]["E02"].org_id
        )
    )
    db.commit()


@pytest.mark.parametrize("count", [0, 1, 3])
def test_other_candidates_and_assignment_keep_requester_private(
    demand: tuple,
    db: Session,
    client: TestClient,
    count: int,
) -> None:
    url, people, auth, body, _ = demand
    set_matches(db, demand, count)
    for offset in range(4):
        db.add(
            CapacityWeek(
                org_id=people["E08"].org_id,
                employee_id=people["E08"].id,
                week_start=week_start(date(2026, 12, 1)) + timedelta(weeks=offset),
                other_percent=0,
                confirmed_by_user_id=demand[-1],
            )
        )
    db.commit()
    profiles = [
        p.model_dump(mode="json")
        for p in db.exec(
            select(ProfileVersion).where(ProfileVersion.org_id == people["E02"].org_id)
        ).all()
    ]
    created = client.post(
        f"{url}/capacity/demands", headers=auth["employee"], json=body
    )
    assert created.status_code == 200, created.text
    path = f"{url}/capacity/demands/{created.json()['id']}"
    basis = client.get(f"{path}/basis", headers=auth["b"])
    assert basis.status_code == 200, basis.text
    expected = [people[code] for code in ("E04", "E05", "E06", "E07", "E08")[count:]]
    assert basis.json()["others"] == [
        {"employee_id": str(p.id), "display_name": p.display_name}
        for p in sorted(expected, key=lambda p: (p.display_name, str(p.id)))
    ]
    assert basis.json()["count"] == count
    assert (
        client.get(f"{path}/basis", headers=auth["admin"]).json()["others"]
        == basis.json()["others"]
    )
    reply = client.post(
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
    assert reply.status_code == 200, reply.text
    accepted = client.post(
        f"{path}/respond",
        headers=auth["employee"],
        json={"action": "accept", "revision": 2},
    )
    assert accepted.status_code == 200, accepted.text
    assigned = client.post(
        f"{path}/assignments",
        headers=auth["b"],
        json={
            "employee_id": str(expected[-1].id),
            "allocation_percent": 20,
            "start_date": "2026-12-01",
            "outside_basis": True,
            "due_date": "2026-12-20",
        },
    )
    assert assigned.status_code == 200, assigned.text
    assert client.get(f"{path}/basis", headers=auth["b"]).json() == basis.json()
    assert profiles == [
        p.model_dump(mode="json")
        for p in db.exec(
            select(ProfileVersion).where(ProfileVersion.org_id == people["E02"].org_id)
        ).all()
    ]
    for role, code in (("employee", "E02"), ("manager", "E01")):
        assert client.get(f"{path}/basis", headers=auth[role]).status_code == 404
        p = principal(db, people[code].org_id, people[code].id)
        headers = initialize(client, token_for(db, people[code])[0])
        assert call(
            client, headers, "get_demand_basis", {"demand_id": created.json()["id"]}
        ).get("isError")
        for name, args, endpoint in (
            ("get_demand", {"demand_id": created.json()["id"]}, path),
            ("list_demands", {}, f"{url}/capacity/demands"),
        ):
            web = client.get(endpoint, headers=auth[role])
            assert web.status_code == 200, web.text
            mcp = call(client, headers, name, args)
            assert not mcp.get("isError"), mcp
            for result in (web.json(), mcp, assistant.call(db, p, [], name, args)):
                raw = json.dumps(result, ensure_ascii=False)
                assert "others" not in raw
                for person in expected:
                    assert person.display_name not in raw
                    assert str(person.id) not in raw
    assert client.get(f"{path}/basis", headers=auth["foreign"]).status_code == 404
