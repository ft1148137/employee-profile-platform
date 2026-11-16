import json
from collections.abc import Generator
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete
from sqlmodel import Session, select

from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition, CapabilitySchemaVersion
from app.domains.capacity.calendar import loads, this_week
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityExtensionRequest,
    CapacityWeek,
)
from app.domains.inbox.models import InboxItem
from tests.api.routes.test_m6_capacity_assignment import case as case


@pytest.mark.parametrize("offset", [None, -1, 8])
def test_start_date_boundary(case: tuple, db: Session, offset: int | None) -> None:
    api, _, demand, people, _, org = case
    start = this_week(db, org.id)
    demand.committed_start_date = start
    db.add(demand)
    db.commit()
    body = {
        "employee_id": str(people["w"].id),
        "allocation_percent": 20,
        "due_date": (start + timedelta(days=7)).isoformat(),
    }
    if offset is not None:
        body["start_date"] = (start + timedelta(days=offset)).isoformat()
    response = api(f"/demands/{demand.id}/assignments", body=body, status=None)
    assert response.status_code in (422, 409), response.text
    if offset is not None:
        api(
            f"/demands/{demand.id}/assignments",
            body={**body, "employee_id": str(people["a"].id)},
            status=404,
        )


def test_outside_basis_requires_confirmation_and_audit(
    case: tuple, db: Session
) -> None:
    api, _, demand, people, _, org = case
    start = this_week(db, org.id)
    schema = CapabilitySchemaVersion(org_id=org.id, version=1, status="active")
    db.add(schema)
    db.flush()
    db.add(
        CapabilityDefinition(
            org_id=org.id,
            schema_version_id=schema.id,
            key="synthetic",
            name="合成能力",
        )
    )
    demand.committed_start_date = None
    db.add(demand)
    db.commit()
    body = {
        "employee_id": str(people["w"].id),
        "allocation_percent": 20,
        "start_date": start.isoformat(),
        "due_date": start.isoformat(),
    }
    path = f"/demands/{demand.id}/assignments"
    assert (
        api(path, body=body, status=409)["detail"]
        == "该员工不在依据中，请确认按你的判断安排"
    )
    api(path, body={**body, "outside_basis": True})
    event = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org.id,
            AuditEvent.action == "capacity.assignment_created",
        )
    ).one()
    assert event.details["outside_basis"] is True


@pytest.fixture
def window(case: tuple, db: Session) -> Generator[tuple]:
    _, _, demand, people, users, org = case
    start = this_week(db, org.id)
    demand.committed_start_date = start
    demand.committed_end_date = start + timedelta(weeks=4, days=4)
    db.add(demand)
    db.execute(delete(CapacityWeek).where(CapacityWeek.org_id == org.id))
    for offset in range(5):
        db.add(
            CapacityWeek(
                org_id=org.id,
                employee_id=people["w"].id,
                week_start=start + timedelta(weeks=offset),
                other_percent=0,
                confirmed_by_user_id=users["bm"].id,
            )
        )
    db.commit()
    yield case
    db.rollback()
    db.execute(
        delete(CapacityExtensionRequest).where(
            CapacityExtensionRequest.org_id == org.id
        )
    )
    db.commit()


def snapshot(db: Session, org_id: UUID) -> str:
    db.expire_all()
    rows = {}
    for model in (
        CapacityDemand,
        CapacityAssignment,
        CapacityDemandEvent,
        CapacityExtensionRequest,
        AuditEvent,
        InboxItem,
    ):
        rows[model.__name__] = sorted(
            (
                r.model_dump(mode="json")
                for r in db.exec(select(model).where(model.org_id == org_id)).all()
            ),
            key=lambda r: r["id"],
        )
    return json.dumps(rows, sort_keys=True)


@pytest.mark.parametrize("kind", ["create", "due", "start", "extension"])
@pytest.mark.parametrize("problem", ["unconfirmed", "stale", "over"])
def test_date_writes_check_whole_window_atomically(
    window: tuple,
    db: Session,
    kind: str,
    problem: str,
) -> None:
    api, _, demand, people, users, org = window
    start = demand.committed_start_date
    end = start + timedelta(weeks=1, days=4)
    checked_week = start if kind == "start" else start + timedelta(weeks=1)
    record = db.exec(
        select(CapacityWeek).where(
            CapacityWeek.org_id == org.id,
            CapacityWeek.employee_id == people["w"].id,
            CapacityWeek.week_start == checked_week,
        )
    ).one()
    if problem == "unconfirmed":
        db.delete(record)
    else:
        if problem == "stale":
            record.confirmed_at = utc_now() - timedelta(days=29)
        else:
            record.other_percent = 110 if kind == "create" else 80
        db.add(record)
    row = CapacityAssignment(
        org_id=org.id,
        demand_id=demand.id,
        employee_id=people["w"].id,
        assigned_by_user_id=users["bm"].id,
        allocation_percent=30,
        start_date=start + timedelta(weeks=1) if kind == "start" else start,
        due_date=end if kind == "start" else start + timedelta(days=4),
    )
    if kind != "create":
        db.add(row)
        db.flush()
    path, body, method, role = (
        f"/assignments/{row.id}",
        {"due_date": end.isoformat()},
        "PATCH",
        "bm",
    )
    if kind == "create":
        path, body, method = (
            f"/demands/{demand.id}/assignments",
            {
                "employee_id": str(people["w"].id),
                "allocation_percent": 30,
                "start_date": start.isoformat(),
                "due_date": end.isoformat(),
                "outside_basis": True,
            },
            "POST",
        )
    elif kind == "start":
        body = {"start_date": start.isoformat()}
    elif kind == "extension":
        pending = CapacityExtensionRequest(
            org_id=org.id,
            assignment_id=row.id,
            requested_due_date=end,
            reason="合成延期",
        )
        db.add(pending)
        db.flush()
        path, body, method, role = (
            f"/extension-requests/{pending.id}/decision",
            {
                "approve": True,
                "new_committed_end_date": (end + timedelta(days=1)).isoformat(),
                "commitment_note": "合成承诺调整",
            },
            "POST",
            "cm",
        )
        from app.domains.review.models import ReviewDelegation

        db.add(
            ReviewDelegation(
                org_id=org.id,
                manager_user_id=users["cm"].id,
                delegate_user_id=users["bm"].id,
                created_by_user_id=users["cm"].id,
                starts_at=utc_now() - timedelta(hours=1),
                ends_at=utc_now() + timedelta(hours=1),
            )
        )
        role = "bm"
        demand.committed_end_date = row.due_date
        db.add(demand)
    db.commit()
    before = snapshot(db, org.id)
    result = api(path, role, body, 409, method)["detail"]
    if problem == "over":
        assert result["over_weeks"] == [
            {
                "week_start": checked_week.isoformat(),
                "over_percent": 40 if kind == "create" else 10,
            }
        ]
    else:
        assert result["weeks"] == [
            {"week_start": checked_week.isoformat(), "status": problem}
        ]
    assert snapshot(db, org.id) == before
    # Recording reality is allowed; confirmation then enables the same commitment.
    api(
        "/weeks",
        body={
            "items": [{"employee_id": str(people["w"].id), "other_percent": 0}],
            "start_week": checked_week.isoformat(),
            "end_week": checked_week.isoformat(),
        },
        method="PUT",
    )
    api(path, role, body, method=method)


@pytest.mark.parametrize("_round", range(20))
@pytest.mark.parametrize("extension_race", [False, True])
def test_concurrent_commitments_cannot_overbook(
    window: tuple,
    db: Session,
    _round: int,
    extension_race: bool,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi import HTTPException

    from app.authz.deps import get_org_principal
    from app.domains.capacity import assignment, extension
    from app.domains.capacity.assignment_schemas import AssignmentInput
    from app.domains.capacity.extension_schemas import ExtensionDecisionInput

    _, _, demand, people, users, org = window
    org_id, employee_id = org.id, people["w"].id
    start = demand.committed_start_date
    end = start + timedelta(days=4)
    second = CapacityDemand(**demand.model_dump(exclude={"id"}))
    db.add(second)
    db.flush()
    pending_id = None
    if extension_race:
        row = CapacityAssignment(
            org_id=org_id,
            demand_id=demand.id,
            employee_id=employee_id,
            assigned_by_user_id=users["bm"].id,
            allocation_percent=60,
            start_date=start,
            due_date=end,
        )
        db.add(row)
        db.flush()
        start, end = start + timedelta(weeks=1), end + timedelta(weeks=1)
        pending = CapacityExtensionRequest(
            org_id=org_id,
            assignment_id=row.id,
            requested_due_date=end,
            reason="合成并发",
        )
        db.add(pending)
        db.flush()
        pending_id = pending.id
    db.commit()
    demand_ids, user_ids = (
        [demand.id, second.id],
        [users["cm" if extension_race else "bm"].id, users["bm"].id],
    )
    barrier = Barrier(2)

    def commit(index: int) -> int:
        with Session(db.get_bind()) as session:
            user = session.get_one(type(users["bm"]), user_ids[index])
            p = get_org_principal(org_id, user, session)
            barrier.wait(timeout=10)
            try:
                if index == 0 and pending_id:
                    extension.decide_extension_request(
                        p, session, pending_id, ExtensionDecisionInput(approve=True)
                    )
                else:
                    assignment.create_assignment(
                        p,
                        session,
                        demand_ids[index],
                        AssignmentInput(
                            employee_id=employee_id,
                            allocation_percent=60,
                            start_date=start,
                            due_date=end,
                            outside_basis=True,
                        ),
                    )
                session.commit()
                return 200
            except HTTPException as exc:
                session.rollback()
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(commit, range(2))) == [200, 409]
    db.expire_all()
    week = loads(db, org_id, [employee_id], start, 1)[employee_id][0]
    assert week.platform_percent == 60
    assert week.platform_percent + week.other_percent <= week.capacity_percent


def test_legacy_start_repair_and_inactive_writes(window: tuple, db: Session) -> None:
    api, _, demand, people, users, org = window
    start = demand.committed_start_date
    row = CapacityAssignment(
        org_id=org.id,
        demand_id=demand.id,
        employee_id=people["w"].id,
        assigned_by_user_id=users["bm"].id,
        allocation_percent=100,
        due_date=start + timedelta(days=4),
    )
    db.add(row)
    db.flush()
    pending = CapacityExtensionRequest(
        org_id=org.id,
        assignment_id=row.id,
        requested_due_date=row.due_date + timedelta(weeks=1),
        reason="合成延期",
    )
    db.add(pending)
    db.commit()
    assert loads(db, org.id, [people["w"].id], start, 1)[people["w"].id][0].legacy_start
    path = f"/assignments/{row.id}"
    for role in ("a", "am", "foreign"):
        api(path, role, {"start_date": start.isoformat()}, 404, "PATCH")
    api(
        path,
        body={"start_date": start.isoformat(), "due_date": row.due_date.isoformat()},
        status=422,
        method="PATCH",
    )
    api(path, body={"start_date": start.isoformat()}, method="PATCH")
    db.expire_all()
    assert not loads(db, org.id, [people["w"].id], start, 1)[people["w"].id][
        0
    ].legacy_start
    people["w"].employment_status = "inactive"
    db.add(people["w"])
    db.commit()
    before = snapshot(db, org.id)
    for body in (
        {"start_date": start.isoformat()},
        {"due_date": row.due_date.isoformat()},
        {"status": "done"},
    ):
        api(path, body=body, status=404, method="PATCH")
    api(f"/extension-requests/{pending.id}/decision", "cm", {"approve": True}, 404)
    assert snapshot(db, org.id) == before
