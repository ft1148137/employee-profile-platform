import json
from copy import deepcopy
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.bot import schemas as s
from app.domains.bot import store, tools
from app.domains.capacity import assistant
from app.domains.capacity.demand_schemas import DemandPublic
from app.domains.capacity.models import CapacityDemandEvent
from app.domains.im import rendering
from app.domains.org.models import Department
from app.domains.profile.models import ProfileVersion
from tests.api.routes.test_m6_capacity_demand import demand as demand
from tests.api.routes.test_m62_small_sample import set_matches
from tests.api.routes.test_mcp import call, initialize, token_for
from tests.domains.test_im_gateway import principal

BANDS = {"available", "full", "overloaded", "unconfirmed"}
WORDS = ("有余量", "基本满", "超负荷", "未确认")


def hierarchy(db: Session, case: tuple) -> tuple[UUID, UUID, UUID]:
    _, people, _, body, _ = case
    parent = UUID(body["target_department_id"])
    children = tuple(
        Department(org_id=people["E04"].org_id, name=name, parent_department_id=parent)
        for name in ("合成子部门一", "合成子部门二")
    )
    db.add_all(children)
    db.flush()
    for code in ("E04", "E05", "E06", "E07"):
        person = people[code]
        person.department_id = parent if code == "E04" else children[0].id
        db.add(person)
        previous = db.exec(
            select(ProfileVersion).where(
                ProfileVersion.org_id == person.org_id,
                ProfileVersion.subject_employee_id == person.id,
                ProfileVersion.revision == 99,
            )
        ).one()
        snapshot = deepcopy(previous.snapshot)
        snapshot["claims"][0]["level"] = (
            "routine" if code == "E04" else "independent_complex"
        )
        db.add(
            ProfileVersion(
                **previous.model_dump(exclude={"id", "revision", "snapshot"}),
                revision=100,
                snapshot=snapshot,
            )
        )
    db.commit()
    return parent, children[0].id, children[1].id


def assert_no_bands(value: dict) -> None:
    raw = json.dumps(value, ensure_ascii=False)
    assert not any(key in raw for key in BANDS), raw
    assert not any(word in raw for word in WORDS), raw


def test_hierarchy_and_level_differences_across_channels(
    demand: tuple, db: Session, client: TestClient
) -> None:
    url, people, auth, body, _ = demand
    parent, child, empty = hierarchy(db, demand)
    p = principal(db, people["E02"].org_id, people["E02"].id)
    headers = initialize(client, token_for(db, people["E02"])[0])
    for target, level, count in (
        (parent, "routine", 4),
        (child, "routine", 3),
        (empty, "routine", 0),
        (parent, "independent_complex", 3),
    ):
        args = {
            "target_department_id": str(target),
            "capability_key": body["capability_key"],
            "minimum_level": level,
        }
        web = client.get(
            f"{url}/capacity/reference", params=args, headers=auth["employee"]
        )
        assert web.status_code == 200, web.text
        mcp = call(client, headers, "capacity_reference", args)
        assert not mcp.get("isError"), mcp
        assert_no_bands(mcp)
        for value in (
            web.json(),
            mcp["structuredContent"],
            assistant.call(db, p, [], "capacity_reference", args),
        ):
            assert value["count"] == count
            assert set(value) == {"department_name", "capability_name", "count"}
            assert_no_bands(value)
        for channel in ("web", "feishu"):
            session = store.create_session(db, p, channel=channel)
            cards = []
            result = tools.call(
                db, p, session, cards, "prepare_demand", {**body, **args}
            )
            assert "error" not in result, result
            assert len(cards) == 1 and isinstance(cards[0], s.CapacityCard)
            assert_no_bands(cards[0].model_dump(mode="json"))
            text, _ = rendering.render(cards[0])
            assert (
                f"即时参考：符合要求 {count} 人（各人负荷只有对方部门经理能看到）"
                in text
            )
        for role in ("b", "admin"):
            own = client.get(
                f"{url}/capacity/reference", params=args, headers=auth[role]
            )
            assert own.status_code == 200, own.text
            assert own.json()["count"] == count
            assert BANDS.issubset(own.json())


def test_reply_basis_keeps_bands_only_for_representatives(
    demand: tuple, db: Session, client: TestClient
) -> None:
    url, people, auth, body, _ = demand
    hierarchy(db, demand)
    created = client.post(
        f"{url}/capacity/demands", headers=auth["employee"], json=body
    )
    assert created.status_code == 200, created.text
    path = f"{url}/capacity/demands/{created.json()['id']}"
    replied = client.post(
        f"{path}/reply",
        headers=auth["b"],
        json={"outcome": "not_now", "text": "合成答复", "revision": 1},
    )
    assert replied.status_code == 200, replied.text
    assert BANDS.issubset(replied.json()["events"][-1]["basis"])
    for role, code in (("employee", "E02"), ("manager", "E01")):
        headers = initialize(client, token_for(db, people[code])[0])
        for endpoint in (path, f"{url}/capacity/demands?side=outgoing"):
            web = client.get(endpoint, headers=auth[role])
            assert web.status_code == 200, web.text
            assert_no_bands(web.json())
            row = web.json()["data"][0] if "data" in web.json() else web.json()
            basis = row["events"][-1]["basis"]
            assert basis["count"] == 4
            assert_no_bands(DemandPublic.model_validate(row).model_dump(mode="json"))
        mcp = call(client, headers, "get_demand", {"demand_id": created.json()["id"]})
        assert not mcp.get("isError"), mcp
        assert mcp["structuredContent"]["events"][-1]["basis"]["count"] == 4
        assert_no_bands(mcp)
    for role in ("b", "admin"):
        own = client.get(path, headers=auth[role])
        assert own.status_code == 200, own.text
        assert BANDS.issubset(own.json()["events"][-1]["basis"])
    stored = db.exec(
        select(CapacityDemandEvent).where(
            CapacityDemandEvent.org_id == people["E02"].org_id,
            CapacityDemandEvent.kind == "replied",
        )
    ).one()
    assert stored.basis == replied.json()["events"][-1]["basis"]


@pytest.mark.parametrize("count", [1, 2])
def test_small_samples_still_hide_counts(
    demand: tuple, db: Session, client: TestClient, count: int
) -> None:
    url, _, auth, body, _ = demand
    set_matches(db, demand, count)
    args = {
        k: body[k] for k in ("target_department_id", "capability_key", "minimum_level")
    }
    result = client.get(
        f"{url}/capacity/reference", params=args, headers=auth["employee"]
    )
    assert result.status_code == 200, result.text
    assert set(result.json()) == {"department_name", "capability_name", "limited"}
    assert result.json()["limited"] is True
