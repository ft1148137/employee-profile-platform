from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import event
from sqlmodel import Session

from app.authz.deps import get_org_principal
from app.domains.base import utc_now
from app.domains.capacity import extension, workload
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityExtensionRequest,
    CapacityWorkloadRequest,
)
from app.domains.review.models import ReviewDelegation
from tests.api.routes.test_m6_capacity_extension import decide, request
from tests.api.routes.test_m62_extension_guidance import case as case


@pytest.mark.parametrize("role", ["cm", "bm"])
def test_commitment_guidance_matches_reviewer(case: tuple, role: str) -> None:
    api, create, _, _, _, _ = case
    pending = request(
        api,
        create("w" if role == "cm" else "w2"),
        "2027-01-05",
        role="w" if role == "cm" else "w2",
    )
    detail = decide(api, pending, role, 409)["detail"]
    if role == "cm":
        assert (
            detail
            == "会推迟对需求方的承诺，你不能确认新的承诺日期：请把审批代理给目标部门经理，或退回让员工改到承诺日期之内"
        )
    else:
        assert (
            detail
            == "会推迟对需求方的承诺：请同时填写新的承诺日期（不早于本需求各分配的完成日期）和对外说明"
        )
        assert "代理" not in detail


def test_assignment_creation_explains_commitment_limit(case: tuple) -> None:
    api, _, demand, people, _, _ = case
    result = api(
        f"/demands/{demand.id}/assignments",
        body={
            "employee_id": str(people["w"].id),
            "allocation_percent": 20,
            "due_date": "2027-01-05",
        },
        status=409,
    )
    assert (
        result["detail"]
        == "完成日期不能晚于对需求方的承诺日期 2026-12-31；需要推迟承诺时，先在已有分配上调整"
    )


def test_only_pending_extensions_have_commitment_confirmers(case: tuple) -> None:
    api, create, _, people, _, _ = case
    row = create()
    history = request(api, row, "2027-01-05")
    api(
        f"/extension-requests/{history['id']}/decision",
        "cm",
        {"approve": False, "note": "请调整日期"},
    )
    pending = request(api, row, "2027-01-06")
    cards = {r["id"]: r for r in api("/extension-requests", "cm")["data"]}
    assert cards[history["id"]]["commitment_confirmer_names"] == []
    assert cards[pending["id"]]["commitment_confirmer_names"] == [
        people["bm"].display_name
    ]


@pytest.mark.parametrize("kind", ["extension", "workload"])
def test_list_name_queries_are_cached(case: tuple, db: Session, kind: str) -> None:
    _, _, demand, people, users, org = case
    db.add(
        ReviewDelegation(
            org_id=org.id,
            manager_user_id=users["cm"].id,
            delegate_user_id=users["w2"].id,
            created_by_user_id=users["cm"].id,
            starts_at=utc_now() - timedelta(hours=1),
            ends_at=utc_now() + timedelta(hours=1),
        )
    )
    db.commit()
    counts = []
    statements = []

    def record_query(*args: Any) -> None:
        statements.append(args[2])

    for batch in (1, 5):
        db.refresh(demand)
        for _ in range(batch):
            if kind == "extension":
                extra_demand = CapacityDemand(**demand.model_dump(exclude={"id"}))
                db.add(extra_demand)
                db.flush()
                assignment = CapacityAssignment(
                    org_id=org.id,
                    demand_id=extra_demand.id,
                    employee_id=people["w"].id,
                    allocation_percent=20,
                    due_date=date(2026, 12, 20),
                    assigned_by_user_id=users["bm"].id,
                )
                db.add(assignment)
                db.flush()
                db.add(
                    CapacityExtensionRequest(
                        org_id=org.id,
                        assignment_id=assignment.id,
                        requested_due_date=date(2027, 1, 5),
                        reason="合成延期",
                    )
                )
            else:
                db.add(
                    CapacityWorkloadRequest(
                        org_id=org.id,
                        employee_id=people["w"].id,
                        requested_percent=40,
                        reason="合成调整",
                        status="returned",
                        decided_by_user_id=users["w2"].id,
                        decided_for_user_id=users["cm"].id,
                    )
                )
        db.commit()
        principal = get_org_principal(org.id, users["w2"], db)
        statements.clear()
        event.listen(db.get_bind(), "before_cursor_execute", record_query)
        try:
            result = (
                extension.list_extension_requests(principal, db)
                if kind == "extension"
                else workload.list_workload_requests(principal, db)
            )
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", record_query)
        counts.append(len(statements))
        assert result.count == (1 if batch == 1 else 6)
        assert all(
            r.on_behalf_of_name == people["cm"].display_name for r in result.data
        )
        if kind == "extension":
            assert all(
                r.commitment_confirmer_names == [people["bm"].display_name]
                for r in result.data
            )
    assert counts[1] == counts[0]
