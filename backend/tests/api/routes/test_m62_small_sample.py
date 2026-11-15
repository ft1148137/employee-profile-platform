import asyncio
import json
import re
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.bot import schemas as s
from app.domains.bot import store, tools
from app.domains.capacity import assistant
from app.domains.capacity.models import CapacityDemandEvent
from app.domains.im import rendering
from app.domains.im.adapters.feishu import Feishu
from app.domains.profile.models import ProfileVersion
from tests.api.routes.test_m6_capacity_demand import demand as demand
from tests.api.routes.test_mcp import call, initialize, token_for
from tests.domains.test_im_gateway import principal

LIMITED = "该部门具备这项能力的人较少，能否承接请提交需求，由对方部门经理答复"
COUNTS = {"count", "available", "full", "overloaded", "unconfirmed"}


def set_matches(db: Session, case: tuple, count: int) -> None:
    people = case[1]
    for i, code in enumerate(("E04", "E05", "E06", "E07")):
        previous = db.exec(
            select(ProfileVersion).where(
                ProfileVersion.org_id == people[code].org_id,
                ProfileVersion.subject_employee_id == people[code].id,
                ProfileVersion.revision == 99,
            )
        ).one()
        snapshot = deepcopy(previous.snapshot) if i < count else {"claims": []}
        db.add(
            ProfileVersion(
                **previous.model_dump(exclude={"id", "revision", "snapshot"}),
                revision=100,
                snapshot=snapshot,
            )
        )
    db.commit()


def assert_reference(value: dict, count: int, *, own: bool = False) -> None:
    if count in (1, 2) and not own:
        assert set(value) == {"department_name", "capability_name", "limited"}
        assert value["limited"] is True
        assert not COUNTS.intersection(value)
    else:
        assert value["count"] == count
        assert COUNTS.issubset(value)


@pytest.mark.parametrize("count", [0, 1, 2, 3])
@pytest.mark.parametrize("role,code", [("employee", "E02"), ("manager", "E01")])
def test_references_and_preview_across_channels(
    demand: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    count: int,
    role: str,
    code: str,
) -> None:
    url, people, auth, body, _ = demand
    set_matches(db, demand, count)
    args = {
        k: body[k] for k in ("target_department_id", "capability_key", "minimum_level")
    }
    web = client.get(f"{url}/capacity/reference", params=args, headers=auth[role])
    assert web.status_code == 200, web.text
    p = principal(db, people[code].org_id, people[code].id)
    headers = initialize(client, token_for(db, people[code])[0])
    mcp = call(client, headers, "capacity_reference", args)
    assert not mcp.get("isError"), mcp
    for result in (
        web.json(),
        mcp["structuredContent"],
        assistant.call(db, p, [], "capacity_reference", args),
    ):
        assert_reference(result, count)
    for channel in ("web", "feishu"):
        session = store.create_session(db, p, channel=channel)
        cards = []
        result = tools.call(db, p, session, cards, "prepare_demand", body)
        assert "error" not in result, result
        assert len(cards) == 1 and isinstance(cards[0], s.CapacityCard)
        text, _ = rendering.render(cards[0])
        if count in (1, 2):
            assert LIMITED in text
            assert not re.search(r"\d+\s*人", text)
            assert not any(
                word in text for word in ("有余量", "基本满", "超负荷", "未确认")
            )
        else:
            assert f"符合要求 {count} 人" in text
        if channel == "feishu":

            async def send(preview_text: str) -> list[dict]:
                sent = []
                adapter = Feishu(
                    p.org_id, {"app_id": "synthetic", "app_secret": "synthetic"}
                )

                async def request(_method: str, _path: str, payload: dict) -> dict:
                    sent.append(payload)
                    return {"data": {"message_id": "synthetic"}}

                monkeypatch.setattr(adapter, "_request", request)
                try:
                    await adapter.render("synthetic-chat", preview_text, [])
                finally:
                    await adapter.close()
                return sent

            sent = asyncio.run(send(text))
            assert (
                text == json.loads(sent[0]["content"])["elements"][0]["text"]["content"]
            )
    for own_role in ("b", "admin"):
        own = client.get(
            f"{url}/capacity/reference", params=args, headers=auth[own_role]
        )
        assert own.status_code == 200, own.text
        assert_reference(own.json(), count, own=True)
    assert (
        client.get(
            f"{url}/capacity/reference", params=args, headers=auth["foreign"]
        ).status_code
        == 404
    )


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_reply_snapshots_are_filtered_only_on_output(
    demand: tuple,
    db: Session,
    client: TestClient,
    count: int,
) -> None:
    url, people, auth, body, _ = demand
    set_matches(db, demand, count)
    created = client.post(
        f"{url}/capacity/demands", headers=auth["employee"], json=body
    )
    assert created.status_code == 200, created.text
    path = f"{url}/capacity/demands/{created.json()['id']}"
    reply = client.post(
        f"{path}/reply",
        headers=auth["b"],
        json={"outcome": "not_now", "text": "合成答复", "revision": 1},
    )
    assert reply.status_code == 200, reply.text
    assert_reference(reply.json()["events"][-1]["basis"], count, own=True)
    for role, code in (("employee", "E02"), ("manager", "E01"), ("b", "E05")):
        headers = initialize(client, token_for(db, people[code])[0])
        p = principal(db, people[code].org_id, people[code].id)
        side = "incoming" if role == "b" else "outgoing"
        for name, args, endpoint in (
            ("get_demand", {"demand_id": created.json()["id"]}, path),
            ("list_demands", {"side": side}, f"{url}/capacity/demands?side={side}"),
        ):
            web = client.get(endpoint, headers=auth[role])
            assert web.status_code == 200, web.text
            mcp = call(client, headers, name, args)
            assert not mcp.get("isError"), mcp
            for result in (
                web.json(),
                mcp["structuredContent"],
                assistant.call(db, p, [], name, args),
            ):
                row = result["data"][0] if name == "list_demands" else result
                assert_reference(row["events"][-1]["basis"], count, own=role == "b")
    stored = db.exec(
        select(CapacityDemandEvent).where(
            CapacityDemandEvent.org_id == people["E02"].org_id,
            CapacityDemandEvent.kind == "replied",
        )
    ).one()
    assert_reference(stored.basis, count, own=True)
