from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, delete, select

from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.change_request import handover
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ProfileChangeRequest,
    RequestStatus,
)
from app.domains.inbox.models import InboxItem
from app.domains.org.models import Department


@pytest.mark.parametrize(
    "case", ["eligible", "13_days", "empty", "opted_out", "department", "draft"]
)
def test_handover_eligibility(
    demo: tuple, db: Session, client: TestClient, case: str
) -> None:
    url, employees, auth = demo
    employee = employees["E02"]
    row = db.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == employee.org_id,
            ProfileChangeRequest.subject_employee_id == employee.id,
        )
    ).one()
    row.created_at = utc_now() - timedelta(
        days=13 if case == "13_days" else 14, minutes=1
    )
    row.updated_at = utc_now()
    revision = row.revision
    if case == "empty":
        ids = select(ChangeItem.id).where(
            ChangeItem.org_id == row.org_id, ChangeItem.request_id == row.id
        )
        db.exec(
            delete(ChangeItemEvidence).where(
                col(ChangeItemEvidence.org_id) == row.org_id,
                col(ChangeItemEvidence.item_id).in_(ids),
            )
        )
        db.exec(
            delete(ChangeItem).where(
                col(ChangeItem.org_id) == row.org_id,
                col(ChangeItem.request_id) == row.id,
            )
        )
    elif case == "opted_out":
        employee.auto_draft_opt_out = True
    elif case == "department":
        department = db.get(Department, employee.department_id)
        assert department
        department.auto_draft_disabled = True
    elif case == "draft":
        row.status = RequestStatus.DRAFT
    db.commit()
    base = f"{url}/initial-profiles/hand-over"
    expected = int(case == "eligible")
    assert client.get(base, headers=auth["admin"]).json() == {"count": expected}
    db.refresh(row)
    assert row.revision == revision
    response = client.post(base, headers=auth["admin"])
    assert response.status_code == 200 and response.json() == {"count": expected}
    db.refresh(row)
    assert row.status == (
        "pending_review"
        if expected
        else "draft"
        if case == "draft"
        else "pending_owner"
    )
    assert row.revision == revision + expected
    audit = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == row.org_id,
            AuditEvent.action == "initial_profiles.handed_over",
        )
    ).all()
    inbox = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == row.org_id,
            InboxItem.request_id == row.id,
            InboxItem.kind == "submitted",
        )
    ).all()
    assert len(audit) == len(inbox) == expected
    if expected:
        assert row.submitted_via == "admin_handover" and row.submitted_at
        assert audit[0].details == {"via": "admin_handover", "owner_reviewed": False}
        assert "员工未过目" in inbox[0].message
        assert client.post(base, headers=auth["admin"]).json() == {"count": 0}
        result = client.post(
            f"{url}/me/requests/{row.id}/withdraw", headers=auth["employee"]
        )
        assert result.status_code == 200 and result.json()["status"] == "withdrawn"


def test_handover_authorization(shared_demo: tuple, client: TestClient) -> None:
    url, _, auth = shared_demo
    for role, status in [("manager", 403), ("employee", 403), ("foreign", 404)]:
        for method in (client.get, client.post):
            assert (
                method(
                    f"{url}/initial-profiles/hand-over", headers=auth[role]
                ).status_code
                == status
            )


def test_handover_is_atomic(
    demo: tuple, db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, employees, auth = demo
    row = db.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == employees["E02"].org_id,
            ProfileChangeRequest.subject_employee_id == employees["E02"].id,
        )
    ).one()
    row.created_at = utc_now() - timedelta(days=15)
    db.commit()

    def fail(*_args: object) -> None:
        raise RuntimeError("Synthetic inbox failure")

    monkeypatch.setattr(handover, "notify", fail)
    with pytest.raises(RuntimeError, match="Synthetic"):
        client.post(f"{url}/initial-profiles/hand-over", headers=auth["admin"])
    db.refresh(row)
    assert row.status == "pending_owner" and row.submitted_via is None
    assert not db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == row.org_id,
            AuditEvent.action == "initial_profiles.handed_over",
        )
    ).all()
