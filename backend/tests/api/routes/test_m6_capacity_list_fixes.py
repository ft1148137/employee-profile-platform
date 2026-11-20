import json
from collections.abc import Generator
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event
from sqlmodel import Session

from app.authz.deps import get_org_principal
from app.authz.principal import Principal
from app.core.security import create_access_token
from app.domains.base import utc_now
from app.domains.capacity import extension, workload
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityExtensionRequest,
    CapacityWorkloadRequest,
)
from app.domains.org.models import EmploymentStatus, RoleGrant
from app.domains.review.models import ReviewDelegation
from tests.api.routes.test_m6_capacity_extension import case as case


@pytest.fixture
def list_case(case: tuple, db: Session) -> Generator[tuple]:
    yield case
    db.rollback()
    db.execute(
        delete(CapacityWorkloadRequest).where(
            CapacityWorkloadRequest.org_id == case[5].id
        )
    )
    db.commit()


@pytest.mark.parametrize("kind", ["workload", "extension"])
@pytest.mark.parametrize("own_history", [False, True])
def test_request_candidates_and_review_cache(
    list_case: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    own_history: bool,
) -> None:
    api, _, demand, people, users, org = list_case
    db.execute(
        delete(RoleGrant).where(
            RoleGrant.org_id == org.id,
            RoleGrant.department_id == people["cm"].department_id,
        )
    )
    model = CapacityWorkloadRequest if kind == "workload" else CapacityExtensionRequest
    assignments = {}
    for code in ("w", "w2", "cm"):
        row = CapacityAssignment(
            org_id=org.id,
            demand_id=demand.id,
            employee_id=people[code].id,
            allocation_percent=20,
            due_date=date(2026, 12, 20),
            assigned_by_user_id=users["bm"].id,
        )
        db.add(row)
        db.flush()
        assignments[code] = row.id

    def add(code: str, index: int, pending: bool) -> UUID:
        fields: dict[str, Any] = (
            {"employee_id": people[code].id, "requested_percent": 50}
            if kind == "workload"
            else {
                "assignment_id": assignments[code],
                "requested_due_date": date(2026, 12, 25),
            }
        )
        row = model(
            org_id=org.id,
            reason="合成原因",
            status="pending" if pending else "returned",
            decided_by_user_id=None
            if pending
            else users["bm" if own_history and index % 2 else "a"].id,
            decided_for_user_id=(
                users["bm"].id
                if own_history and not pending and index % 2 == 0
                else None
            ),
            created_at=utc_now() + timedelta(seconds=index),
            **fields,
        )
        db.add(row)
        return row.id

    pending = {add("w", -10, True)}
    history = [add("w", i, False) for i in range(105 if own_history else 60)]
    db.commit()
    principal = get_org_principal(org.id, users["bm"], db)
    loaded = []

    def record_load(_session: Session, instance: object) -> None:
        if isinstance(instance, model):
            loaded.append(instance.id)

    calls: list[set[UUID]] = []
    module = workload if kind == "workload" else extension
    list_requests = (
        workload.list_workload_requests
        if kind == "workload"
        else extension.list_extension_requests
    )
    original = module.reviewable_employees

    def counted(
        session: Session, actor: Principal, employee_ids: set[UUID]
    ) -> dict[UUID, UUID | None]:
        calls.append(employee_ids)
        return original(session, actor, employee_ids)

    def record_query(*args: Any) -> None:
        statements.append(args[2])

    def measured_query_count() -> int:
        statements.clear()
        event.listen(db.get_bind(), "before_cursor_execute", record_query)
        try:
            assert list_requests(principal, db).count > 0
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", record_query)
        return len(statements)

    statements: list[str] = []
    monkeypatch.setattr(module, "reviewable_employees", counted)
    baseline = measured_query_count()
    assert calls == [{people["w"].id}]

    pending.update(add(code, -10, True) for code in ("w2", "cm"))
    db.commit()
    calls.clear()
    assert measured_query_count() == baseline
    assert calls == [
        {person.id for code, person in people.items() if code in assignments}
    ]

    event.listen(db, "loaded_as_persistent", record_load)
    try:
        result = list_requests(principal, db)
    finally:
        event.remove(db, "loaded_as_persistent", record_load)
    assert {r.id for r in result.data if r.status == "pending"} == pending
    assert [r.id for r in result.data if r.status != "pending"] == (
        list(reversed(history[-100:])) if own_history else []
    )
    assert result.count == (103 if own_history else 3)
    assert len(loaded) <= (103 if own_history else 3)
    assert api(f"/{kind}-requests", "am")["data"] == []


def test_demand_pagination_batches_timeline_and_preserves_privacy(
    list_case: tuple, db: Session, client: TestClient
) -> None:
    _, _, demand, people, users, org = list_case
    rows = [demand]
    for i in range(119):
        row = CapacityDemand(
            **demand.model_dump(exclude={"id", "created_at"}),
            created_at=demand.created_at + timedelta(seconds=i + 1),
        )
        db.add(row)
        rows.append(row)
    db.flush()
    for row in rows:
        db.add(
            CapacityDemandEvent(
                org_id=org.id,
                demand_id=row.id,
                kind="replied",
                actor_user_id=users["bm"].id,
                text="合成部门答复",
                via="web",
                outcome="can_do",
            )
        )
    db.commit()
    expected = [str(row.id) for row in reversed(rows)]
    headers = {
        "Authorization": f"Bearer {create_access_token(users['a'].id, expires_delta=timedelta(minutes=10))}"
    }
    url = f"/api/v1/orgs/{org.id}/capacity/demands"
    statements = []

    def record_query(*args: Any) -> None:
        statements.append(args[2])

    event.listen(db.get_bind(), "before_cursor_execute", record_query)
    try:
        for params, ids in (
            ({}, expected[:50]),
            ({"limit": 100}, expected[:100]),
            ({"skip": 100}, expected[100:]),
            ({"skip": 120}, []),
        ):
            statements.clear()
            response = client.get(url, headers=headers, params=params)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["count"] == 120
            assert [r["id"] for r in body["data"]] == ids
            assert all(r["events"][0]["responder_name"] is None for r in body["data"])
            assert sum(
                "FROM capacity_demand_event" in sql for sql in statements
            ) == bool(ids)
            assert sum(
                'FROM org_membership JOIN "user"' in sql for sql in statements
            ) == bool(ids)
            serialized = json.dumps(body, ensure_ascii=False)
            for code in ("w", "w2", "cm"):
                person = people[code]
                assert all(
                    str(value) not in serialized
                    for value in (person.id, person.display_name, person.employee_code)
                )
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record_query)
    for params in ({"limit": 101}, {"limit": 0}, {"skip": -1}):
        assert client.get(url, headers=headers, params=params).status_code == 422


def test_actionable_demands_match_can_reply_and_count(
    list_case: tuple, db: Session, client: TestClient
) -> None:
    api, _, demand, people, users, org = list_case

    def add(requester: str, requesting: str, target: str) -> str:
        row = CapacityDemand(
            **demand.model_dump(
                exclude={
                    "id",
                    "status",
                    "requester_user_id",
                    "requesting_department_id",
                    "target_department_id",
                    "committed_start_date",
                    "committed_end_date",
                }
            ),
            status="submitted",
            requester_user_id=users[requester].id,
            requesting_department_id=people[requesting].department_id,
            target_department_id=people[target].department_id,
        )
        db.add(row)
        return str(row.id)

    child = add("a", "am", "cm")
    parent = add("a", "am", "bm")
    manager_self = add("bm", "bm", "cm")
    admin_self = add("admin", "am", "bm")
    outside = add("bm", "bm", "am")
    db.commit()

    expected = {
        ("bm", "incoming"): {child, parent, admin_self},
        ("bm", "outgoing"): set(),
        ("cm", "incoming"): {child, manager_self},
        ("admin", "incoming"): {child, parent, manager_self, outside},
        ("admin", "outgoing"): {child, parent, manager_self, outside},
        ("a", "incoming"): set(),
        ("a", "outgoing"): set(),
    }
    url = f"/api/v1/orgs/{org.id}/capacity/demands"
    for (role, side), ids in expected.items():
        headers = {
            "Authorization": f"Bearer {create_access_token(users[role].id, expires_delta=timedelta(minutes=10))}"
        }
        params = {"side": side}
        default = client.get(url, headers=headers, params=params).json()
        assert (
            client.get(
                url, headers=headers, params={**params, "actionable_only": "false"}
            ).json()
            == default
        )
        filtered = [row for row in default["data"] if row["can_reply"] is True]
        assert {row["id"] for row in filtered} == ids
        for skip in (0, 1, len(ids) + 1):
            response = client.get(
                url,
                headers=headers,
                params={**params, "actionable_only": "true", "limit": 1, "skip": skip},
            )
            assert response.status_code == 200, response.text
            assert response.json() == {
                "data": filtered[skip : skip + 1],
                "count": len(ids),
            }

    assert (
        api("/demands?side=incoming&status=submitted&actionable_only=true", "bm")[
            "count"
        ]
        == 3
    )
    assert api("/demands?status=accepted&actionable_only=true", "admin") == {
        "data": [],
        "count": 0,
    }
    assert api(
        "/demands?side=incoming&actionable_only=true", "foreign", other_org=True
    ) == {
        "data": [],
        "count": 0,
    }


@pytest.mark.parametrize("delegate", ["a", "w2"])
def test_extension_delegates_keep_department_restriction(
    list_case: tuple, db: Session, delegate: str
) -> None:
    api, create, _, _, users, org = list_case
    row = create()
    db.add(
        ReviewDelegation(
            org_id=org.id,
            manager_user_id=users["cm"].id,
            delegate_user_id=users[delegate].id,
            created_by_user_id=users["cm"].id,
            starts_at=utc_now() - timedelta(hours=1),
            ends_at=utc_now() + timedelta(hours=1),
        )
    )
    db.commit()
    pending = api(
        f"/assignments/{row['id']}/extension-requests",
        "w",
        {"requested_due_date": "2026-12-25", "reason": "PRIVATE_REASON"},
    )
    result = api("/extension-requests", delegate)
    assert result["count"] == (0 if delegate == "a" else 1)
    if delegate == "a":
        assert "PRIVATE_REASON" not in json.dumps(result)
        api(
            f"/extension-requests/{pending['id']}/decision",
            delegate,
            {"approve": True},
            404,
        )
    assert api("/extension-requests", "w")["data"] == [
        {**pending, "reviewer_names": []}
    ]
    assert api("/extension-requests", "foreign", status=404) == {
        "detail": "未找到该企业或你没有权限访问"
    }


def test_team_workloads_exclude_inactive_employees(
    list_case: tuple, db: Session
) -> None:
    api, _, _, people, _, _ = list_case
    people["w"].employment_status = EmploymentStatus.INACTIVE
    db.add(people["w"])
    db.commit()
    for role in ("admin", "bm"):
        body = api("/workloads", role)
        assert str(people["w"].id) not in {r["employee_id"] for r in body["data"]}
        assert str(people["w2"].id) in {r["employee_id"] for r in body["data"]}
