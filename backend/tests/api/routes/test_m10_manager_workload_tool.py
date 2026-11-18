from dataclasses import replace
from typing import Literal

import pytest
from pydantic import JsonValue
from sqlmodel import Session

from app.domains.bot import store, tools
from app.domains.bot.team_workload import DISABLED, NOT_FOUND
from app.domains.org.models import EmploymentStatus
from tests.api.routes.test_m82_assistant_copy import m82 as m82
from tests.domains.test_im_gateway import principal


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_named_workload_respects_scope_and_module(
    m82: tuple, db: Session, channel: Literal["web", "feishu"]
) -> None:
    people, args, setting = m82
    org = people["E02"].org_id
    for code in ("E02", "E03", "E04", "E05", "E10"):
        people[code].display_name = f"合成员工{code}"
        db.add(people[code])
    db.commit()
    manager = principal(db, org, people["E05"].id)
    employee = principal(db, org, people["E03"].id)
    manager_session = store.create_session(db, manager, channel)
    employee_session = store.create_session(db, employee, channel)
    maximum = replace(manager, is_org_admin=True)
    definitions = tools.definitions(maximum, db=db, channel=channel)
    assert len({tool.name for tool in definitions}) == len(definitions) == 29
    assert not {"get_named_workload", "prepare_project_draft"} & {
        tool.name for tool in definitions
    }

    scope: set[str] = set()

    def query(name: str, *, own: bool = False, **arguments: JsonValue) -> JsonValue:
        return tools.call(
            db,
            employee if own else manager,
            employee_session if own else manager_session,
            [],
            "coordination",
            {
                "function": "get_named_workload",
                "arguments": {"name": name, "weeks": 1, **arguments},
            },
            on_read=scope.update,
        )

    result = query(
        people["E04"].display_name, department_id=args["target_department_id"]
    )
    assert isinstance(result, dict)
    assert result["employee_name"] == people["E04"].display_name
    assert isinstance(result["weeks"], list) and len(result["weeks"]) == 1
    week = result["weeks"][0]
    assert isinstance(week, dict)
    assert week["other_percent"] == 0 and week["platform_percent"] == 0
    assert week["status"] == "confirmed"
    assert scope == {str(people["E04"].id)}
    assert "employee_id" not in result
    assert query(people["E10"].display_name) != {"error": NOT_FOUND}
    for name in (people["E02"].display_name, "不存在的合成员工"):
        assert query(name) == {"error": NOT_FOUND}
    assert query(
        people["E02"].display_name, department_id=args["requesting_department_id"]
    ) == {"error": NOT_FOUND}

    people["E10"].display_name = people["E04"].display_name
    db.add(people["E10"])
    db.commit()
    assert query(people["E04"].display_name) == {
        "error": "有多位同名员工，请补充部门后再查"
    }
    assert (
        query(people["E04"].display_name, department_id=args["target_department_id"])
        == result
    )

    people["E04"].employment_status = EmploymentStatus.INACTIVE
    db.add(people["E04"])
    db.commit()
    assert query(
        people["E04"].display_name, department_id=args["target_department_id"]
    ) == {"error": NOT_FOUND}
    assert query(people["E05"].display_name, own=True) == {"error": NOT_FOUND}
    own = query(people["E03"].display_name, own=True)
    assert isinstance(own, dict) and own["employee_name"] == people["E03"].display_name

    setting.enabled = False
    db.add(setting)
    db.commit()
    assert query(people["E04"].display_name) == {"error": DISABLED}
    assert all(
        tool.name != "coordination"
        for tool in tools.definitions(manager, db=db, channel=channel)
    )
