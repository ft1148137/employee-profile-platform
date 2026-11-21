from collections import Counter
from collections.abc import Generator
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.core.security import create_access_token
from app.domains.capacity.models import (
    CapacityDemand,
    CapacityDemandDependency,
    CapacityDemandEvent,
    CapacityProject,
    CapacitySetting,
)
from app.domains.im.models import ImBinding, ImNotification
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.org.models import Department, Employee, OrgMembership, RoleGrant
from app.models import User


@pytest.fixture
def project_data(
    demo: tuple,
    db: Session,
    client: TestClient,
) -> Generator[tuple[str, dict[str, dict[str, str]], dict[str, Any], list[UUID]]]:
    url, employees, auth = demo
    org = employees["E01"].org_id
    actor_ids = []
    departments = []
    for index in range(4):
        department = Department(org_id=org, name=f"合成目标部门{index}")
        user = User(username=f"{uuid4()}@example.com", hashed_password="unused")
        db.add_all([department, user])
        db.flush()
        person = Employee(
            org_id=org,
            department_id=department.id,
            display_name=f"合成私密答复人{index}",
        )
        db.add(person)
        db.flush()
        if index == 0:
            db.add(
                ImBinding(
                    org_id=org,
                    employee_id=person.id,
                    platform="feishu",
                    external_user_id="synthetic-user",
                    external_chat_id="synthetic-chat",
                    bot_identity="synthetic-bot",
                )
            )
        membership = OrgMembership(org_id=org, user_id=user.id, employee_id=person.id)
        db.add(membership)
        db.flush()
        db.add(
            RoleGrant(
                org_id=org,
                membership_id=membership.id,
                role="dept_manager",
                department_id=department.id,
            )
        )
        auth[f"b{index}"] = {
            "Authorization": f"Bearer {create_access_token(user.id, expires_delta=timedelta(minutes=5))}"
        }
        actor_ids.append(user.id)
        departments.append(department.id)
    admin = db.exec(select(User).where(User.email == "admin@demo.example.com")).one()
    db.add(CapacitySetting(org_id=org, enabled=True, updated_by_user_id=admin.id))
    db.commit()
    capabilities = client.get(f"{url}/capabilities", headers=auth["manager"]).json()[
        "data"
    ]
    body: dict[str, Any] = {
        "title": "合成交付项目",
        "description": "私有项目说明",
        "requesting_department_id": str(employees["E01"].department_id),
        "roles": [
            {
                "role_name": f"合成角色{index}",
                "target_department_id": str(departments[index % 3]),
                "capability_key": capabilities[0]["key"],
                "headcount": 1,
                "allocation_percent": 50,
                "window_start": "2026-12-01",
                "window_end": "2026-12-03",
                "description": f"合成部门需求{index}",
                "depends_on": [index - 1] if index else [],
                **(
                    {
                        "extra_capabilities": [
                            {
                                "key": capabilities[1]["key"],
                                "minimum_level": None,
                                "required": False,
                            }
                        ]
                    }
                    if not index
                    else {}
                ),
            }
            for index in range(4)
        ],
    }
    yield f"{url}/capacity", auth, body, actor_ids
    db.rollback()
    for model in (
        CapacityDemandDependency,
        CapacityDemandEvent,
        CapacityDemand,
        CapacityProject,
        CapacitySetting,
    ):
        db.execute(delete(model).where(model.org_id == org))
    db.commit()


def assert_no_project_submission(db: Session, org: UUID) -> None:
    for model in (
        CapacityProject,
        CapacityDemand,
        CapacityDemandEvent,
        CapacityDemandDependency,
        ImNotification,
    ):
        assert db.exec(select(model).where(model.org_id == org)).all() == []
    assert (
        db.exec(
            select(InboxItem.id).where(
                InboxItem.org_id == org, InboxItem.kind == InboxKind.CAPACITY
            )
        ).all()
        == []
    )


def test_project_submission_is_atomic(
    project_data: tuple,
    client: TestClient,
    db: Session,
) -> None:
    base, auth, body, actors = project_data
    roles = body["roles"]
    original = roles[-1]["target_department_id"]
    roles[-1]["target_department_id"] = str(uuid4())
    bad = client.post(f"{base}/projects", headers=auth["manager"], json=body)
    assert bad.status_code == 404, bad.text
    org = UUID(base.split("/")[-2])
    assert_no_project_submission(db, org)
    notices = select(InboxItem.recipient_user_id).where(
        InboxItem.org_id == org, InboxItem.kind == InboxKind.CAPACITY
    )
    roles[-1]["target_department_id"] = original
    response = client.post(f"{base}/projects", headers=auth["manager"], json=body)
    assert response.status_code == 200, response.text
    assert (
        len(db.exec(select(CapacityDemand).where(CapacityDemand.org_id == org)).all())
        == 4
    )
    assert (
        len(
            db.exec(
                select(CapacityDemandDependency).where(
                    CapacityDemandDependency.org_id == org
                )
            ).all()
        )
        == 3
    )
    assert Counter(db.exec(notices).all()) == Counter(
        {actors[0]: 2, actors[1]: 1, actors[2]: 1}
    )
    assert (
        len(db.exec(select(ImNotification).where(ImNotification.org_id == org)).all())
        == 2
    )
    assert (
        db.exec(
            select(CapacityDemand).where(
                CapacityDemand.org_id == org, CapacityDemand.role_name == "合成角色0"
            )
        )
        .one()
        .extra_capabilities
        == roles[0]["extra_capabilities"]
    )


@pytest.mark.parametrize(
    ("removed", "message"),
    [
        (True, "不在当前启用的能力词典中"),
        (False, "最低等级"),
    ],
    ids=["removed-optional-capability", "removed-minimum-level"],
)
def test_project_rejects_stale_extra_capability(
    project_data: tuple, client: TestClient, db: Session, removed: bool, message: str
) -> None:
    base, auth, body, _ = project_data
    dictionary_url = f"{base.removesuffix('/capacity')}/capabilities"
    editor = client.get(f"{dictionary_url}/editor", headers=auth["admin"])
    assert editor.status_code == 200, editor.text
    dictionary = editor.json()
    definitions = dictionary["definitions"]
    extra = definitions[2]
    levels = extra["level_rubric"]["levels"]
    last_role = body["roles"][-1]
    last_role["extra_capabilities"] = [
        {
            "key": extra["key"],
            "minimum_level": None if removed else levels[-1]["key"],
            "required": not removed,
        }
    ]
    if removed:
        definitions.remove(extra)
    else:
        levels.pop()
    updated = client.post(
        f"{dictionary_url}/versions",
        headers=auth["admin"],
        json={
            "expected_version": dictionary["expected_version"],
            "definitions": definitions,
        },
    )
    assert updated.status_code == 200, updated.text

    response = client.post(f"{base}/projects", headers=auth["manager"], json=body)
    assert response.status_code == 422, response.text
    assert last_role["role_name"] in response.json()["detail"]
    assert message in response.json()["detail"]
    assert_no_project_submission(db, UUID(base.split("/")[-2]))


@pytest.mark.parametrize(
    "invalid", ["cycle", "foreign_dependency", "six", "duplicate", "window"]
)
def test_project_rejects_invalid_roles(
    project_data: tuple, client: TestClient, invalid: str
) -> None:
    base, auth, body, _ = project_data
    first = body["roles"][0]
    if invalid == "cycle":
        first["depends_on"] = [1]
    elif invalid == "foreign_dependency":
        first["depends_on"] = [str(uuid4())]
    elif invalid == "six":
        first["extra_capabilities"] = [
            {"key": str(i), "required": True} for i in range(6)
        ]
    elif invalid == "duplicate":
        first["extra_capabilities"] = [
            {"key": first["capability_key"], "required": True}
        ]
    else:
        first["window_end"] = "2026-11-30"
    response = client.post(f"{base}/projects", headers=auth["manager"], json=body)
    assert response.status_code == 422, response.text


def test_project_views_keep_department_boundaries(
    project_data: tuple,
    client: TestClient,
    db: Session,
) -> None:
    base, auth, body, actors = project_data
    created = client.post(f"{base}/projects", headers=auth["manager"], json=body)
    assert created.status_code == 200, created.text
    url = f"{base}/projects/{created.json()['id']}"
    a = client.get(url, headers=auth["manager"]).json()
    first, second, *_ = a["roles"]
    response = client.post(
        f"{base}/demands/{first['id']}/reply",
        headers=auth["b0"],
        json={
            "outcome": "not_now",
            "reason_code": "scope",
            "text": "需要另行确认交付条件",
            "revision": 1,
        },
    )
    assert response.status_code == 200, response.text
    responder_name = response.json()["events"][-1]["responder_name"]
    assert responder_name == "合成私密答复人0"
    responder = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == UUID(base.split("/")[-2]),
            OrgMembership.user_id == actors[0],
        )
    ).one()
    assert responder.employee_id is not None
    a = client.get(url, headers=auth["manager"])
    assert a.status_code == 200, a.text
    event = a.json()["roles"][0]["demand"]["events"][-1]
    assert event["responder_name"] is None
    assert set(event["basis"]) == {"department_name", "capability_name", "count"}
    b = client.get(url, headers=auth["b1"])
    assert b.status_code == 200, b.text
    assert b.json()["roles"][0]["status"] == "replied"
    assert all(row["demand"] is None for row in b.json()["roles"])
    assert "需要另行确认交付条件" not in b.text
    assert body["description"] not in b.text
    for side in (a, b):
        assert responder_name not in side.text
        assert str(responder.employee_id) not in side.text
        assert not any(str(user_id) in side.text for user_id in actors)

    extra_key = body["roles"][0]["extra_capabilities"][0]["key"]
    capability = next(
        item
        for item in client.get(
            f"{base.removesuffix('/capacity')}/capabilities", headers=auth["manager"]
        ).json()["data"]
        if item["key"] == extra_key
    )
    expected_extras = [
        {
            "key": extra_key,
            "capability_name": capability["name"],
            "minimum_level": None,
            "minimum_level_label": None,
            "required": False,
        }
    ]
    assert a.json()["roles"][0]["demand"]["extra_capabilities"] == expected_extras
    for viewer in ("manager", "b0"):
        response = client.get(f"{base}/demands/{first['id']}", headers=auth[viewer])
        assert response.status_code == 200, response.text
        assert response.json()["extra_capabilities"] == expected_extras
    assert (
        client.get(f"{base}/demands/{first['id']}", headers=auth["foreign"]).status_code
        == 404
    )

    for role in ("manager", "b1"):
        detail = client.get(f"{base}/demands/{second['id']}", headers=auth[role])
        assert detail.status_code == 200, detail.text
        project = detail.json()["project"]
        assert project["role_name"] == second["role_name"]
        assert project["prerequisites"] == [
            {k: v for k, v in first.items() if k not in {"demand", "outcome"}}
            | {"status": "replied"}
        ]
        assert responder_name not in str(project)
    listing = client.get(f"{base}/demands", headers=auth["manager"]).json()
    assert {row["project"]["title"] for row in listing["data"]} == {body["title"]}
    assert client.get(f"{base}/projects", headers=auth["manager"]).json() == [
        created.json()
    ]
    assert client.get(f"{base}/projects", headers=auth["b1"]).json() == []
    for viewer in ("b3", "employee", "foreign"):
        assert client.get(url, headers=auth[viewer]).status_code == 404


@pytest.mark.parametrize(
    ("outcome", "dates", "reason", "earliest", "finish", "delay"),
    [
        ("not_now", {}, "not_now", None, None, None),
        (
            "can_do",
            {"start_date": "2026-12-01", "end_date": "2026-12-05"},
            "late_commitment",
            "2026-12-06",
            "2026-12-11",
            8,
        ),
        (
            "partial",
            {"start_date": "2026-12-01", "end_date": "2026-12-03"},
            "partial",
            "2026-12-04",
            "2026-12-09",
            6,
        ),
    ],
)
def test_project_schedule_and_downstream_impacts(
    project_data: tuple,
    client: TestClient,
    outcome: str,
    dates: dict[str, str],
    reason: str,
    earliest: str | None,
    finish: str | None,
    delay: int | None,
) -> None:
    base, auth, body, _ = project_data
    body["roles"][1]["depends_on"] = []
    body["roles"][2]["depends_on"] = [0, 1]
    created = client.post(f"{base}/projects", headers=auth["manager"], json=body)
    assert created.status_code == 200, created.text
    url = f"{base}/projects/{created.json()['id']}"
    roles = client.get(url, headers=auth["manager"]).json()["roles"]
    response = client.post(
        f"{base}/demands/{roles[0]['id']}/reply",
        headers=auth["b0"],
        json={
            "outcome": outcome,
            "text": "已核对交付条件",
            "revision": 1,
            **dates,
            **({"reason_code": "scope"} if outcome == "not_now" else {}),
        },
    )
    assert response.status_code == 200, response.text
    schedule = client.get(url, headers=auth["manager"]).json()["schedule"]
    assert schedule == client.get(url, headers=auth["b0"]).json()["schedule"]
    stages = schedule["stages"]
    assert [[r["role"]["role_name"] for r in stage] for stage in stages] == [
        ["合成角色0", "合成角色1"],
        ["合成角色2"],
        ["合成角色3"],
    ]
    assert stages[1][0]["earliest_start"] == earliest
    unconfirmed = outcome == "not_now"
    assert schedule["forecast_unconfirmed"] is unconfirmed
    assert stages[2][0]["earliest_start"] == (None if unconfirmed else "2026-12-04")
    assert stages[2][0]["forecast_end"] == finish
    impact = schedule["impacts"][0]
    assert impact["reasons"] == [reason]
    assert [
        (r["role_name"], r["target_department_name"]) for r in impact["downstream"]
    ] == [
        ("合成角色2", "合成目标部门2"),
        ("合成角色3", "合成目标部门0"),
    ]
    assert schedule["delay_days"] == delay
    if unconfirmed:
        assert schedule["forecast_end"] is None
        for part in (stages[0][0], stages[1][0], stages[2][0]):
            assert part["forecast_unconfirmed"]
            assert part["planned_start"] and part["planned_end"]
            assert part["forecast_start"] is part["forecast_end"] is None
        assert stages[0][1]["forecast_end"] == "2026-12-03"
        demand_url = f"{base}/demands/{roles[0]['id']}"
        follow_up = client.post(
            f"{demand_url}/respond",
            headers=auth["manager"],
            json={
                "action": "follow_up",
                "revision": response.json()["revision"],
                "text": "请重新确认日期",
            },
        )
        assert follow_up.status_code == 200, follow_up.text
        assert client.get(url, headers=auth["manager"]).json()["schedule"][
            "forecast_unconfirmed"
        ]
        reply = client.post(
            f"{demand_url}/reply",
            headers=auth["b0"],
            json={
                "revision": follow_up.json()["revision"],
                "outcome": "can_do",
                "text": "可以承接",
                "start_date": "2026-12-01",
                "end_date": "2026-12-05",
            },
        )
        assert reply.status_code == 200, reply.text
        restored = client.get(url, headers=auth["manager"]).json()["schedule"]
        assert not restored["forecast_unconfirmed"]
        assert (restored["forecast_end"], restored["delay_days"]) == ("2026-12-11", 8)


def test_project_analysis_rejects_external_and_cyclic_graphs() -> None:
    from datetime import date

    from app.domains.capacity.demand_schemas import ProjectRoleSummary
    from app.domains.capacity.models import DemandStatus
    from app.domains.capacity.project import InvalidProjectDependencies, analyze_project

    a, b, foreign = uuid4(), uuid4(), uuid4()
    roles = [
        ProjectRoleSummary(
            id=role_id,
            role_name=name,
            target_department_name="合成部门",
            status=DemandStatus.SUBMITTED,
            window_start=date(2026, 12, 1),
            window_end=date(2026, 12, 3),
            committed_start_date=None,
            committed_end_date=None,
        )
        for role_id, name in ((a, "开发"), (b, "测试"))
    ]
    with pytest.raises(InvalidProjectDependencies, match="同一项目"):
        analyze_project(roles, {a: [foreign]}, {})
    with pytest.raises(InvalidProjectDependencies, match="循环"):
        analyze_project(roles, {a: [b], b: [a]}, {})
