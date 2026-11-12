from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.core.db import engine
from app.domains.audit.models import AuditEvent
from app.domains.change_request import employee
from app.domains.change_request.models import (
    ChangeItemEvidence,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.change_request.schemas import ItemInput
from app.domains.employee_tools import EMPLOYEE_TOOLS
from app.domains.evidence.models import Evidence, EvidenceStatus
from app.domains.org.models import Employee, OrgMembership
from app.models import User


def payload(key: str = "automated_testing", **changes: object) -> dict:
    return {"op": "add", "capability_key": key, "statement": "合成测试判断", **changes}


def principal_for(db: Session, org_id: UUID) -> employee.Principal:
    user = db.exec(
        select(User).where(User.email == "employee.platform@demo.example.com")
    ).one()
    return get_org_principal(org_id, user, db)


def test_my_reads(shared_demo: tuple, client: TestClient) -> None:
    url, employees, auth = shared_demo
    for path in (
        "profile",
        "evidence",
        "requests",
        "capabilities",
        "inbox",
        "questions",
    ):
        response = client.get(f"{url}/me/{path}", headers=auth["employee"])
        assert response.status_code == 200
        if path == "profile":
            assert response.json()["employee"]["id"] == str(employees["E02"].id)
        assert (
            client.get(f"{url}/me/{path}", headers=auth["foreign"]).status_code == 404
        )
        admin_response = client.get(f"{url}/me/{path}", headers=auth["admin"])
        assert admin_response.status_code == (200 if path == "inbox" else 404)
        if path == "inbox":
            assert admin_response.json()["data"] == []
    for query in ("limit=0", "limit=101", "skip=-1"):
        assert (
            client.get(
                f"{url}/me/evidence?{query}", headers=auth["employee"]
            ).status_code
            == 422
        )
        assert (
            client.get(f"{url}/me/inbox?{query}", headers=auth["employee"]).status_code
            == 422
        )
        assert (
            client.get(
                f"{url}/me/questions?{query}", headers=auth["employee"]
            ).status_code
            == 422
        )


def test_draft_preview_submit_and_withdraw(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    headers = auth["employee"]
    original_profile = client.get(f"{url}/me/profile", headers=headers).json()
    own = client.get(f"{url}/me/evidence", headers=headers).json()["data"][0]["id"]
    body = {
        "items": [
            payload(
                owner_statement="本人确认的依据",
                objection="本人对初始推断的异议",
                evidence_ids=[own],
            )
        ]
    }
    response = client.post(f"{url}/me/requests", headers=headers, json=body)
    assert response.status_code == 201, response.text
    draft = response.json()
    base = f"{url}/me/requests/{draft['id']}"
    assert draft["status"] == "draft" and draft["revision"] == 1
    for role in ("admin", "manager"):
        rows = client.get(
            f"{url}/employees/{employees['E02'].id}/change-requests", headers=auth[role]
        ).json()["data"]
        assert draft["id"] not in {r["id"] for r in rows}
    before = client.get(f"{base}/preview", headers=headers).json()
    assert before["items"][0]["before"] is None
    assert before["items"][0]["after"]["after"]["assessment_status"] == "tentative"
    body["items"][0]["statement"] = "预览后修改的合成判断"
    edited = client.put(f"{base}/items", headers=headers, json=body).json()
    assert edited["revision"] == 2
    stale = client.post(
        f"{base}/submit", headers=headers, json={"revision": before["revision"]}
    )
    assert (
        stale.status_code == 409 and stale.json()["detail"] == "内容已变化，请重新预览"
    )
    preview = client.get(f"{base}/preview", headers=headers).json()
    submitted = client.post(
        f"{base}/submit", headers=headers, json={"revision": preview["revision"]}
    )
    assert submitted.status_code == 200, submitted.text
    result = submitted.json()
    assert (
        result["status"] == "pending_review"
        and result["submitted_via"] == "web"
        and result["submitted_at"]
    )
    assert result["items"][0]["after"] == preview["items"][0]["after"]["after"]
    item = result["items"][0]
    links = db.exec(
        select(ChangeItemEvidence).where(
            ChangeItemEvidence.org_id == employees["E02"].org_id,
            ChangeItemEvidence.item_id == UUID(item["id"]),
        )
    ).all()
    reports = db.exec(
        select(Evidence).where(
            Evidence.org_id == employees["E02"].org_id,
            Evidence.source == "self_report:web",
        )
    ).all()
    assert len(reports) == 2
    assert {link.relation for link in links} == {"supports", "contradicts"}
    contents = {r.id: r.content.copy() for r in reports}
    for report in reports:
        assert report.visibility == "restricted" and report.kind == "self_report"
        assert report.source_ref == item["id"]
        assert report.provenance == {
            "via": "web",
            "client_name": None,
            "request_id": draft["id"],
        }
        for role in ("admin", "manager", "employee"):
            detail = client.get(
                f"{url}/evidence/{report.id}", headers=auth[role]
            ).json()
            assert (detail["content"] is None) == (role == "admin")
            rows = client.get(
                f"{url}/employees/{employees['E02'].id}/change-requests",
                headers=auth[role],
            ).json()["data"]
            visible = next(r for r in rows if r["id"] == draft["id"])["items"][0]
            assert visible["owner_statement"] == (
                None if role == "admin" else "本人确认的依据"
            )
            assert visible["objection"] == (
                None if role == "admin" else "本人对初始推断的异议"
            )
    assert client.put(f"{base}/items", headers=headers, json=body).status_code == 409
    assert (
        client.post(
            f"{base}/submit", headers=headers, json={"revision": result["revision"]}
        ).status_code
        == 409
    )
    withdrawn = client.post(f"{base}/withdraw", headers=headers)
    assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "withdrawn"
    assert client.post(f"{base}/withdraw", headers=headers).status_code == 409
    db.expire_all()
    for report in reports:
        assert (
            report.content == contents[report.id]
            and report.status == EvidenceStatus.RETRACTED
        )
    actions = [
        e.action
        for e in db.exec(
            select(AuditEvent).where(AuditEvent.org_id == employees["E02"].org_id)
        ).all()
    ]
    assert actions.count("evidence.retracted") == 2
    assert (
        "change_request.submitted" in actions and "change_request.withdrawn" in actions
    )
    assert client.get(f"{url}/me/profile", headers=headers).json() == original_profile
    inbox = client.get(f"{url}/me/inbox", headers=headers).json()
    assert {i["kind"] for i in inbox["data"]} == {
        "submitted",
        "status_changed",
        "question",
        "initial_review",
    }
    inbox_id = inbox["data"][0]["id"]
    first = client.post(f"{url}/me/inbox/{inbox_id}/read", headers=headers).json()
    second = client.post(f"{url}/me/inbox/{inbox_id}/read", headers=headers).json()
    assert first["read_at"] and first["read_at"] == second["read_at"]
    assert (
        client.post(
            f"{url}/me/inbox/{inbox_id}/read", headers=auth["manager"]
        ).status_code
        == 404
    )


@pytest.mark.parametrize("edit", [False, True])
def test_initial_request_confirmation(
    demo: tuple, client: TestClient, edit: bool
) -> None:
    url, _, auth = demo
    headers = auth["employee"]
    initial = next(
        r
        for r in client.get(f"{url}/me/requests", headers=headers).json()["data"]
        if r["origin"] == "initial_sync"
    )
    base = f"{url}/me/requests/{initial['id']}"
    if edit:
        uncertainty = initial["items"][0]["uncertainty"]
        body = {
            "items": [
                payload(objection="不认可系统起草的范围", uncertainty=uncertainty)
            ]
        }
        updated = client.put(f"{base}/items", headers=headers, json=body)
        assert updated.status_code == 200
        initial = updated.json()
        assert initial["status"] == "draft"
        assert initial["items"][0]["uncertainty"] == uncertainty
        assert (
            client.get(f"{url}/me/profile", headers=headers).json()["employee"][
                "progress"
            ]["review_status"]
            == "pending_employee"
        )
    else:
        assert client.post(f"{base}/withdraw", headers=headers).status_code == 409
    preview = client.get(f"{base}/preview", headers=headers).json()
    result = client.post(
        f"{base}/submit", headers=headers, json={"revision": preview["revision"]}
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "pending_review"
    inbox = client.get(f"{url}/me/inbox", headers=headers).json()
    initial_notification = next(
        row
        for row in inbox["data"]
        if row["kind"] == "initial_review" and row["request_id"] == initial["id"]
    )
    assert initial_notification["done_at"] and initial_notification["read_at"]
    if edit:
        assert any(
            e["relation"] == "contradicts"
            for e in result.json()["items"][0]["evidence"]
        )


@pytest.mark.parametrize("role", ["employee", "manager", "admin", "foreign"])
def test_other_employee_and_tenant_requests(
    demo: tuple, client: TestClient, db: Session, role: str
) -> None:
    url, employees, auth = demo
    other = employees["E03"]
    row = ProfileChangeRequest(
        org_id=other.org_id,
        subject_employee_id=other.id,
        origin=RequestOrigin.MANUAL,
        status=RequestStatus.DRAFT,
        author={},
    )
    db.add(row)
    db.commit()
    base = f"{url}/me/requests/{row.id}"
    headers = auth[role]
    assert client.get(f"{base}/preview", headers=headers).status_code == 404
    assert (
        client.put(
            f"{base}/items", headers=headers, json={"items": [payload()]}
        ).status_code
        == 404
    )
    assert (
        client.post(f"{base}/submit", headers=headers, json={"revision": 1}).status_code
        == 404
    )
    assert client.post(f"{base}/withdraw", headers=headers).status_code == 404
    # A principal in another tenant cannot use a request belonging to this tenant.
    if role == "foreign":
        user_id = UUID(
            jwt.decode(
                headers["Authorization"].removeprefix("Bearer "),
                options={"verify_signature": False},
            )["sub"]
        )
        membership = db.exec(
            select(OrgMembership).where(OrgMembership.user_id == user_id)
        ).one()
        foreign_employee = Employee(
            org_id=membership.org_id, display_name="Synthetic foreign employee"
        )
        db.add(foreign_employee)
        db.flush()
        membership.employee_id = foreign_employee.id
        db.commit()
        foreign = base.replace(str(other.org_id), str(membership.org_id))
        assert client.get(f"{foreign}/preview", headers=headers).status_code == 404
        assert (
            client.put(
                f"{foreign}/items", headers=headers, json={"items": [payload()]}
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"{foreign}/submit", headers=headers, json={"revision": 1}
            ).status_code
            == 404
        )
        assert client.post(f"{foreign}/withdraw", headers=headers).status_code == 404


def test_validation_and_current_targets(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    headers = auth["employee"]
    evidence = db.exec(
        select(Evidence).where(
            Evidence.org_id == employees["E03"].org_id,
            Evidence.subject_employee_id == employees["E03"].id,
        )
    ).first()
    response = client.post(
        f"{url}/me/requests",
        headers=headers,
        json={
            "items": [
                payload("missing"),
                payload(level="missing"),
                payload("incident_support", evidence_ids=[str(evidence.id)]),
                payload("rknn_deployment", op="modify"),
                payload("rknn_deployment", op="retract"),
            ]
        },
    )
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert {e["index"] for e in errors} == {0, 1, 2, 3, 4}
    assert all(any("\u4e00" <= ch <= "\u9fff" for ch in e["message"]) for e in errors)
    for extra in (
        {"via": "mcp"},
        {"employee_id": str(employees["E03"].id)},
        {"assessment_status": "confirmed"},
    ):
        body = {"items": [payload(**extra)]}
        assert (
            client.post(f"{url}/me/requests", headers=headers, json=body).status_code
            == 422
        )
    assert (
        client.post(
            f"{url}/me/requests",
            headers=headers,
            json={"items": [payload()], "via": "mcp"},
        ).status_code
        == 422
    )
    profile = client.get(f"{url}/me/profile", headers=headers).json()
    current = next(i for i in profile["items"] if i["claim"])
    key, claim = current["capability"]["key"], current["claim"]
    assert (
        client.post(
            f"{url}/me/requests", headers=headers, json={"items": [payload(key)]}
        ).status_code
        == 422
    )
    for op in ("modify", "retract", "attach_evidence"):
        response = client.post(
            f"{url}/me/requests",
            headers=headers,
            json={"items": [payload(key, op=op, level=claim["level"])]},
        )
        assert response.status_code == 201, response.text
        draft = response.json()
        assert draft["items"][0]["target_claim_id"] == claim["id"]
        preview = client.get(
            f"{url}/me/requests/{draft['id']}/preview", headers=headers
        ).json()
        assert preview["items"][0]["before"]["id"] == claim["id"]
        assert (
            client.post(
                f"{url}/me/requests/{draft['id']}/withdraw", headers=headers
            ).json()["status"]
            == "withdrawn"
        )
    empty = client.post(
        f"{url}/me/requests", headers=headers, json={"items": []}
    ).json()
    assert (
        client.post(
            f"{url}/me/requests/{empty['id']}/submit",
            headers=headers,
            json={"revision": empty["revision"]},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "via,origin",
    [("mcp", "conversation"), ("import", "import"), ("bot", "conversation")],
)
def test_other_channels_use_shared_service(
    demo: tuple, db: Session, via: employee.EmployeeVia, origin: str
) -> None:
    _, employees, _ = demo
    principal = principal_for(db, employees["E02"].org_id)
    draft = employee.save_draft(
        principal,
        db,
        None,
        [ItemInput(**payload(owner_statement="合成经历"))],
        via,
        "测试客户端",
    )
    assert draft.origin == origin
    submitted = employee.submit(
        principal, db, draft.id, draft.revision, via, "测试客户端"
    )
    db.commit()
    from app.domains.inbox.service import my_inbox

    notification = my_inbox(principal, db).data[0]
    assert notification.request_id == submitted.id
    assert ("测试客户端" in notification.message) == (via != "bot")
    employee.withdraw(principal, db, draft.id)
    db.commit()


def test_submit_rolls_back_with_notifications(
    demo: tuple, client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, employees, auth = demo
    draft = client.post(
        f"{url}/me/requests",
        headers=auth["employee"],
        json={"items": [payload(owner_statement="必须原子写入")]},
    ).json()

    def fail(*_args: object) -> None:
        raise RuntimeError("Synthetic notification failure")

    monkeypatch.setattr(employee, "notify", fail)
    with pytest.raises(RuntimeError, match="Synthetic"):
        client.post(
            f"{url}/me/requests/{draft['id']}/submit",
            headers=auth["employee"],
            json={"revision": draft["revision"]},
        )
    db.expire_all()
    assert db.get(ProfileChangeRequest, UUID(draft["id"])).status == "draft"
    assert not db.exec(
        select(Evidence).where(
            Evidence.org_id == employees["E02"].org_id,
            Evidence.source == "self_report:web",
        )
    ).all()
    assert not db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == employees["E02"].org_id,
            AuditEvent.action == "change_request.submitted",
        )
    ).all()


def test_tools_exclude_identity_and_bot_submission() -> None:
    assert len(EMPLOYEE_TOOLS) == 11
    for tool in EMPLOYEE_TOOLS:
        properties = tool.input_model.model_json_schema()["properties"]
        assert not {"employee_id", "org_id", "via", "client_name"} & properties.keys()
        assert tool.input_model.model_json_schema()["additionalProperties"] is False
        assert tool.bot_allowed == (
            tool.name not in ("submit_request", "withdraw_request", "appeal_decision")
        )
        assert tool.writes == (
            tool.name
            in (
                "draft_update_request",
                "submit_request",
                "withdraw_request",
                "answer_question",
                "appeal_decision",
            )
        )


def test_concurrent_submission_is_once(demo: tuple, client: TestClient) -> None:
    url, _, auth = demo
    headers = auth["employee"]
    draft = client.post(
        f"{url}/me/requests",
        headers=headers,
        json={"items": [payload(owner_statement="并发提交的确认依据")]},
    ).json()

    def send() -> int:
        return client.post(
            f"{url}/me/requests/{draft['id']}/submit",
            headers=headers,
            json={"revision": draft["revision"]},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: send(), range(2)))
    assert sorted(results) == [200, 409]
    notifications = client.get(f"{url}/me/inbox", headers=headers).json()["data"]
    assert (
        sum(
            n["request_id"] == draft["id"] and n["kind"] == "submitted"
            for n in notifications
        )
        == 1
    )


def test_validation_queries_are_batched(demo: tuple, db: Session) -> None:
    _, employees, _ = demo
    principal = principal_for(db, employees["E02"].org_id)
    dictionary = employee.capability_dictionary(principal, db)
    claims = {
        i.capability.key for i in employee.my_profile(principal, db).items if i.claim
    }
    keys = [c.key for c in dictionary.data if c.key not in claims]
    assert len(keys) >= 3
    queries: list[str] = []

    def count(_conn: object, _cursor: object, statement: str, *_: object) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    counts = []
    for size in (1, 3):
        queries.clear()
        event.listen(engine, "before_cursor_execute", count)
        try:
            draft = employee.save_draft(
                principal,
                db,
                None,
                [ItemInput(**payload(key)) for key in keys[:size]],
                "web",
            )
            assert len(employee.preview(principal, db, draft.id).items) == size
        finally:
            event.remove(engine, "before_cursor_execute", count)
        counts.append(len(queries))
    assert counts[0] == counts[1]
    db.rollback()
