from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.core.db import engine
from app.domains.base import utc_now
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.evidence.models import Evidence, EvidenceKind
from app.domains.profile.models import EvidenceRelation


@pytest.mark.parametrize("role", ["admin", "manager", "employee", "foreign"])
def test_visibility(shared_demo: tuple, client: TestClient, role: str) -> None:
    url, employees, auth = shared_demo
    response = client.get(
        f"{url}/employees/{employees['E02'].id}/change-requests", headers=auth[role]
    )
    assert response.status_code == (404 if role == "foreign" else 200)
    if role == "foreign":
        return
    data = response.json()
    assert data["count"] == 1
    request = data["data"][0]
    assert request["origin"] == "initial_sync" and request["status"] == "pending_owner"
    assert set(request["generated_by"]) == {"mode", "model"}
    assert [i["capability"]["key"] for i in request["items"]] == [
        "automated_testing",
        "incident_support",
    ]
    for item in request["items"]:
        assert item["op"] == "add" and item["target_claim_id"] is None
        assert item["after"]["assessment_status"] == "tentative"
        assert item["after"]["level"] is None
        assert item["explanation"] and item["uncertainty"]
        evidence = item["evidence"][0]
        assert not evidence["redacted"] and evidence["occurred_at"]
        assert evidence["relation"] == "context" and "content" not in evidence
    if role == "admin":
        return
    denied = employees["E05" if role == "manager" else "E03"]
    assert (
        client.get(
            f"{url}/employees/{denied.id}/change-requests", headers=auth[role]
        ).status_code
        == 404
    )


def test_redaction(demo: tuple, client: TestClient, db: Session) -> None:
    url, employees, auth = demo
    employee = employees["E02"]
    request = db.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == employee.org_id,
            ProfileChangeRequest.subject_employee_id == employee.id,
        )
    ).one()
    item = db.exec(
        select(ChangeItem)
        .where(ChangeItem.request_id == request.id)
        .order_by(ChangeItem.position)
    ).first()
    evidence = db.exec(
        select(Evidence).where(
            Evidence.org_id == employee.org_id,
            Evidence.subject_employee_id == employee.id,
            Evidence.kind == EvidenceKind.SELF_REPORT,
        )
    ).one()
    db.add(
        ChangeItemEvidence(
            org_id=employee.org_id,
            item_id=item.id,
            evidence_id=evidence.id,
            relation=EvidenceRelation.SUPPORTS,
        )
    )
    request.generated_by = {"mode": "rules", "model": None, "prompt_version": "private"}
    db.add(request)
    db.commit()
    for role in ("admin", "manager", "employee"):
        data = client.get(
            f"{url}/employees/{employee.id}/change-requests", headers=auth[role]
        ).json()
        assert data["data"][0]["generated_by"] == {"mode": "rules", "model": None}
        ref = next(
            ref
            for ref in data["data"][0]["items"][0]["evidence"]
            if ref["kind"] == "self_report"
        )
        assert ref["redacted"] == (role == "admin")
        assert ref["title"] == (
            "员工自述（受限）" if role == "admin" else evidence.title
        )


def test_status_order_and_empty(demo: tuple, client: TestClient, db: Session) -> None:
    url, employees, auth = demo
    employee = employees["E02"]
    for offset, status in enumerate(RequestStatus):
        db.add(
            ProfileChangeRequest(
                org_id=employee.org_id,
                subject_employee_id=employee.id,
                origin=RequestOrigin.MANUAL,
                status=status,
                author={"actor_type": "seed"},
                created_at=utc_now() + timedelta(days=offset + 1),
            )
        )
    db.commit()
    base = f"{url}/employees/{employee.id}/change-requests"
    opened = client.get(base, headers=auth["employee"]).json()
    assert opened["count"] == 4
    assert {row["status"] for row in opened["data"]} == {
        "draft",
        "pending_owner",
        "pending_review",
    }
    all_rows = client.get(f"{base}?status=all", headers=auth["employee"]).json()
    assert all_rows["count"] == 8
    dates = [row["created_at"] for row in all_rows["data"]]
    assert dates == sorted(dates, reverse=True)
    assert (
        client.get(f"{base}?status=invalid", headers=auth["employee"]).status_code
        == 422
    )
    assert client.get(
        f"{url}/employees/{employees['E12'].id}/change-requests", headers=auth["admin"]
    ).json() == {"data": [], "count": 0}
    assert (
        client.get(
            f"{url}/employees/{employees['E07'].id}/change-requests",
            headers=auth["admin"],
        ).json()["data"][0]["status"]
        == "pending_review"
    )


def test_query_count(demo: tuple, client: TestClient, db: Session) -> None:
    url, employees, auth = demo
    employee = employees["E02"]
    base = f"{url}/employees/{employee.id}/change-requests"
    queries: list[object] = []

    def count_query(*args: object) -> None:
        queries.append(args)

    counts = []
    for size in (2, 20):
        event.listen(engine, "before_cursor_execute", count_query)
        try:
            response = client.get(base, headers=auth["employee"])
        finally:
            event.remove(engine, "before_cursor_execute", count_query)
        assert response.status_code == 200
        assert len(response.json()["data"][0]["items"]) == size
        counts.append(len(queries))
        queries.clear()
        if size == 20:
            continue
        request = db.exec(
            select(ProfileChangeRequest).where(
                ProfileChangeRequest.org_id == employee.org_id,
                ProfileChangeRequest.subject_employee_id == employee.id,
            )
        ).one()
        original = db.exec(
            select(ChangeItem).where(ChangeItem.request_id == request.id)
        ).first()
        link = db.exec(
            select(ChangeItemEvidence).where(ChangeItemEvidence.item_id == original.id)
        ).one()
        for position in range(2, 20):
            extra = ChangeItem(
                **original.model_dump(exclude={"id", "created_at", "position"}),
                position=position,
            )
            db.add(extra)
            db.flush()
            db.add(
                ChangeItemEvidence(
                    org_id=employee.org_id,
                    item_id=extra.id,
                    evidence_id=link.evidence_id,
                    relation=link.relation,
                )
            )
        db.commit()
    assert counts[0] == counts[1]
    assert counts[0] <= 10
