"""Model-visible contracts must describe the same inputs the dispatcher validates."""

import json
from dataclasses import replace

import pytest
from sqlmodel import Session

from app.domains.bot import store, tools
from app.domains.planning.tools import COORDINATION_TOOLS
from tests.domains.test_im_gateway import principal

pytest_plugins = ("tests.api.routes.test_m8_coordination_tools",)


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_model_sees_registered_argument_schemas(
    coordination: tuple, db: Session, channel: str
) -> None:
    people, _, demand = coordination
    manager = principal(db, demand.org_id, people["E05"].id)
    manager = replace(manager, is_org_admin=True)
    definitions = tools.definitions(manager, db=db, channel=channel)
    assert len(definitions) <= 29
    definition = next(t for t in definitions if t.name == "coordination")
    contracts = json.loads(definition.description.split("\n")[-1])
    assert set(contracts) == {t.name for t in COORDINATION_TOOLS}
    for tool in COORDINATION_TOOLS:
        assert contracts[tool.name] == {
            "description": tool.description,
            "parameters": tool.input_model.model_json_schema(),
        }
    simulation = contracts["simulate_plan"]["parameters"]
    assert simulation["required"] == ["demand_id"]
    assert simulation["properties"]["demand_id"]["format"] == "uuid"
    assert simulation["properties"]["assignees"]["default"] is None
    assignee = simulation["$defs"]["AssigneeInput"]
    assert set(assignee["required"]) == {
        "employee_id",
        "allocation_percent",
        "start_date",
        "end_date",
    }
    assert assignee["properties"]["allocation_percent"]["minimum"] == 10
    assert assignee["properties"]["allocation_percent"]["maximum"] == 100
    assert assignee["properties"]["start_date"]["format"] == "date"
    comparison = contracts["compare_plan_options"]["parameters"]
    assert set(comparison["required"]) == {"case_id", "option_ids"}
    assert comparison["properties"]["option_ids"]["minItems"] == 1
    assert "首次模拟只传 demand_id" in definition.description
    assert "assignees" in definition.description


def test_initial_custom_and_comparison_arguments(
    coordination: tuple, db: Session
) -> None:
    people, _, demand = coordination
    manager = principal(db, demand.org_id, people["E05"].id)
    session = store.create_session(db, manager)

    def run(function: str, arguments: dict) -> dict:
        result = tools.call(
            db,
            manager,
            session,
            [],
            "coordination",
            {
                "function": function,
                "arguments": arguments,
            },
        )
        assert isinstance(result, dict) and "error" not in result
        return result

    initial = run("simulate_plan", {"demand_id": str(demand.id)})
    custom = run(
        "simulate_plan",
        {
            "demand_id": str(demand.id),
            "assignees": [
                {
                    "employee_id": str(people["E04"].id),
                    "allocation_percent": 50,
                    "start_date": demand.window_start.isoformat(),
                    "end_date": demand.window_end.isoformat(),
                }
            ],
        },
    )
    option_ids = [initial["options"][0]["id"], custom["option"]["id"]]
    comparison = run(
        "compare_plan_options",
        {
            "case_id": initial["case_id"],
            "option_ids": option_ids,
        },
    )
    assert [option["id"] for option in comparison["options"]] == option_ids
