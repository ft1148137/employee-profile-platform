from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.service import get_schema
from app.domains.planning.simulator import simulate
from app.domains.planning.validator import validate
from tests.api.routes.test_m7_planning_simulator import data, option, person
from tests.api.routes.test_m7_planning_validator import params
from tests.api.routes.test_m7_planning_validator import planning_setup as planning_setup

REQUIRED = {
    "key": "device-debug",
    "name": "设备调试",
    "minimum_level": "independent",
    "required": True,
    "levels": ["guided", "independent"],
}
OPTIONAL = {
    "key": "protocol-trace",
    "name": "协议追踪",
    "minimum_level": "independent",
    "required": False,
    "levels": ["guided", "independent", "advanced"],
}


@pytest.mark.parametrize("optional_free", [80, 79])
def test_required_capabilities_filter_and_optional_capabilities_break_ties(
    optional_free: int,
) -> None:
    snapshot = data(headcount=2)
    review = {
        "key": "design-review",
        "name": "设计评审",
        "minimum_level": None,
        "required": True,
        "levels": ["routine"],
    }
    snapshot["demand"]["extra_capabilities"] = [REQUIRED, review, OPTIONAL]
    below, unknown, basic, extra, partial = [str(UUID(int=i)) for i in range(1, 6)]
    required_levels = {"device-debug": "independent", "design-review": "routine"}
    snapshot["candidates"] = [
        {
            **person(below, remaining=100),
            "extra_levels": {**required_levels, "device-debug": "guided"},
        },
        {
            **person(unknown, remaining=100),
            "extra_levels": {**required_levels, "device-debug": None},
        },
        {
            **person(partial, remaining=100),
            "extra_levels": {"device-debug": "independent"},
        },
        {**person(basic), "extra_levels": required_levels},
        {
            **person(extra, remaining=optional_free),
            "extra_levels": {
                **required_levels,
                "protocol-trace": "advanced",
            },
        },
    ]
    options, _ = simulate(snapshot)
    direct = option(options, "direct")
    assert direct["verdict"] == "feasible"
    assert [row["employee_id"] for row in direct["params"]["assignees"]] == (
        [extra, basic] if optional_free == 80 else [basic, extra]
    )
    assert direct["summary"]["matched_extra_capabilities"] == {
        basic: ["设备调试", "设计评审"],
        extra: ["设备调试", "设计评审", "协议追踪"],
    }


@pytest.mark.parametrize(
    ("required_level", "optional_level", "verdict", "messages"),
    [
        ("guided", "independent", "infeasible", ["必需能力未达标：设备调试"]),
        (None, "independent", "insufficient_data", ["必需能力未确认：设备调试"]),
        ("independent", "guided", "feasible", ["可选能力未达标：协议追踪"]),
        ("independent", None, "feasible", ["可选能力未确认：协议追踪"]),
    ],
)
def test_validator_distinguishes_unmet_and_unconfirmed_capabilities(
    required_level: str | None,
    optional_level: str | None,
    verdict: str,
    messages: list[str],
) -> None:
    snapshot = data()
    snapshot["demand"]["extra_capabilities"] = [REQUIRED, OPTIONAL]
    employee_id = str(UUID(int=1))
    snapshot["candidates"] = [
        {
            **person(employee_id),
            "extra_levels": {
                "device-debug": required_level,
                "protocol-trace": optional_level,
            },
        }
    ]
    result = validate(snapshot, params(employee_id=employee_id))
    assert result.verdict == verdict
    assert [row["message"] for row in result.findings] == messages
    assert result.summary["conditions"] == []


def test_multi_capability_role_requires_a_published_primary_level() -> None:
    snapshot = data()
    snapshot["demand"].update(capability_name="合成交付", extra_capabilities=[REQUIRED])
    employee_id = str(UUID(int=1))
    snapshot["candidates"] = [
        {
            **person(employee_id),
            "level": None,
            "extra_levels": {"device-debug": "independent"},
        }
    ]
    assert option(simulate(snapshot)[0], "direct")["params"]["assignees"] == []
    result = validate(snapshot, params(employee_id=employee_id))
    assert result.verdict == "insufficient_data"
    assert [row["message"] for row in result.findings] == ["必需能力未确认：合成交付"]


def test_extra_capability_edit_stales_snapshot_and_preserves_visibility(
    planning_setup: tuple, db: Session, client: TestClient
) -> None:
    url, employees, auth, demand = planning_setup
    schema = get_schema(db, demand.org_id)
    db.add(
        CapabilityDefinition(
            org_id=demand.org_id,
            schema_version_id=schema.id,
            key=REQUIRED["key"],
            name=REQUIRED["name"],
            level_rubric={"levels": [{"key": level} for level in REQUIRED["levels"]]},
        )
    )
    demand.extra_capabilities = [
        {field: REQUIRED[field] for field in ("key", "required", "minimum_level")}
    ]
    db.add(demand)
    db.commit()
    create_url = f"{url}/planning/demands/{demand.id}/cases"
    response = client.post(create_url, headers=auth["b"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["stale"] is False
    assert result["data"]["demand"]["extra_capabilities"] == [REQUIRED]
    candidate = next(
        row
        for row in result["data"]["candidates"]
        if row["employee_id"] == str(employees["E04"].id)
    )
    assert candidate["extra_levels"] == {REQUIRED["key"]: None}
    case_url = f"{url}/planning/cases/{result['id']}"
    for viewer in ("manager", "employee", "foreign"):
        assert client.get(case_url, headers=auth[viewer]).status_code == 404
    demand.extra_capabilities = [{**demand.extra_capabilities[0], "required": False}]
    db.add(demand)
    db.commit()
    stale = client.get(case_url, headers=auth["b"])
    assert stale.status_code == 200
    assert stale.json()["stale"] is True
    assert stale.json()["data"] == result["data"]
    refreshed = client.post(create_url, headers=auth["b"])
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["stale"] is False
    assert refreshed.json()["snapshot_id"] != result["snapshot_id"]
