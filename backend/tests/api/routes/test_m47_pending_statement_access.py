import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.security import create_access_token
from app.domains.bot import schemas as s
from app.domains.bot import store, tools
from app.domains.change_request.models import (
    ChangeItem,
    ItemDecision,
    ProfileChangeRequest,
    RequestStatus,
)
from app.domains.im import rendering
from app.domains.org.models import OrgMembership, Role, RoleGrant
from app.domains.review import service
from tests.api.routes.test_mcp import call, initialize, token_for
from tests.api.routes.test_review_delegations import setup_delegation
from tests.api.routes.test_reviews import actor, manager, pending, values

NOTICE = "未发布的陈述仅本人、管辖经理和审批人可见"


@pytest.mark.parametrize(
    "status",
    ["pending_owner", "pending_review", "changes_requested", "rejected", "withdrawn"],
)
def test_non_reviewer_admin_cannot_read_unpublished_statement(
    db: Session, demo: tuple, client: TestClient, status: str
) -> None:
    _, request = pending(db, demo)
    row = db.get(ProfileChangeRequest, request.id)
    row.status = RequestStatus(status)
    db.add(row)
    item_row = db.get(ChangeItem, request.items[0].id)
    item_row.objection = "合成异议"
    item_row.decision_reason = "合成审批原因"
    db.add(item_row)
    db.commit()
    db.refresh(item_row)
    original = item_row.model_dump()
    response = client.get(
        f"{demo[0]}/employees/{demo[1]['E02'].id}/change-requests?status=all",
        headers=demo[2]["admin"],
    )
    assert response.status_code == 200
    item = next(r for r in response.json()["data"] if r["id"] == str(request.id))[
        "items"
    ][0]
    assert item["after"]["statement"] == NOTICE
    assert request.items[0].after.statement not in response.text
    assert item["after"]["level"] == "routine"
    assert (
        item["after"]["assessment_status"] == request.items[0].after.assessment_status
    )
    assert item["capability"]["key"] == "backend_api"
    assert (
        item["owner_statement"] is item["objection"] is item["decision_reason"] is None
    )
    db.expire_all()
    assert db.get(ChangeItem, request.items[0].id).model_dump() == original


def test_mixed_approved_request_only_exposes_approved_statement(
    db: Session, demo: tuple, client: TestClient
) -> None:
    _, request = pending(
        db,
        demo,
        items=[
            {
                "op": "modify",
                "capability_key": "backend_api",
                "statement": "批准的合成陈述",
                "level": "routine",
            },
            {
                "op": "add",
                "capability_key": "automated_testing",
                "statement": "退回的合成陈述",
            },
        ],
    )
    service.decide(
        manager(db, demo),
        db,
        request.id,
        request.revision,
        [
            *values(request)[:1],
            values(request, ItemDecision.CHANGES_REQUESTED)[1],
        ],
    )
    db.commit()
    response = client.get(
        f"{demo[0]}/employees/{demo[1]['E02'].id}/change-requests?status=all",
        headers=demo[2]["admin"],
    )
    result = next(r for r in response.json()["data"] if r["id"] == str(request.id))
    assert result["status"] == "approved"
    assert result["items"][0]["after"]["statement"] == "批准的合成陈述"
    assert result["items"][1]["after"]["statement"] == NOTICE
    assert "退回的合成陈述" not in response.text


@pytest.mark.parametrize(
    "role",
    [
        "employee",
        "manager",
        "delegate",
        "outsider_manager",
        "outsider_employee",
        "foreign",
    ],
)
def test_list_visibility(
    db: Session, demo: tuple, client: TestClient, role: str
) -> None:
    _, request = pending(db, demo)
    headers = demo[2].get(role)
    if role == "delegate":
        setup_delegation(db, demo)
        headers = demo[2]["admin"]
    elif role.startswith("outsider"):
        p = actor(
            db,
            demo[1]["E02"].org_id,
            "employee.algorithm@demo.example.com",
        )
        if role == "outsider_manager":
            subject = demo[1]["E05"]
            member = db.exec(
                select(OrgMembership).where(
                    OrgMembership.org_id == p.org_id,
                    OrgMembership.user_id == p.user_id,
                )
            ).one()
            db.add(
                RoleGrant(
                    org_id=p.org_id,
                    membership_id=member.id,
                    role=Role.DEPT_MANAGER,
                    department_id=subject.department_id,
                )
            )
            db.commit()
        headers = {
            "Authorization": f"Bearer {create_access_token(p.user_id, expires_delta=timedelta(minutes=5))}"
        }
    response = client.get(
        f"{demo[0]}/employees/{demo[1]['E02'].id}/change-requests?status=all",
        headers=headers,
    )
    if role.startswith("outsider") or role == "foreign":
        assert response.status_code == 404
        assert request.items[0].after.statement not in response.text
    else:
        assert response.status_code == 200
        result = next(r for r in response.json()["data"] if r["id"] == str(request.id))
        assert (
            result["items"][0]["after"]["statement"] == request.items[0].after.statement
        )


@pytest.mark.parametrize("channel", ["web", "feishu", "mcp"])
@pytest.mark.parametrize("fallback", [False, True])
def test_admin_review_tools_require_actual_assignment(
    db: Session, demo: tuple, client: TestClient, channel: str, fallback: bool
) -> None:
    _, request = pending(
        db, demo, role="manager.platform" if fallback else "employee.platform"
    )
    subject = demo[1]["E05"]
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == subject.org_id,
            OrgMembership.employee_id == subject.id,
        )
    ).one()
    db.add(
        RoleGrant(org_id=subject.org_id, membership_id=member.id, role=Role.ORG_ADMIN)
    )
    db.commit()
    admin = actor(db, subject.org_id, "employee.algorithm@demo.example.com")
    statement = request.items[0].after.statement
    args = {"request_id": str(request.id)}
    if channel == "mcp":
        headers, _ = token_for(db, subject)
        result = call(client, initialize(client, headers), "get_review", args)
        assert bool(result.get("isError")) == (not fallback)
        assert (statement in json.dumps(result, ensure_ascii=False)) == fallback
        if fallback:
            decided = call(
                client,
                initialize(client, headers),
                "decide_review_items",
                {
                    **args,
                    "revision": request.revision,
                    "decisions": [v.model_dump(mode="json") for v in values(request)],
                },
            )
            assert not decided.get("isError")
    else:
        session = store.create_session(db, admin, channel)
        cards: list[s.Message] = []
        result = tools.call(db, admin, session, cards, "get_review", args)
        assert (result is not None) == fallback
        assert (statement in json.dumps(result, ensure_ascii=False)) == fallback
        if fallback:
            tools.call(
                db,
                admin,
                session,
                cards,
                "prepare_review_decision",
                {
                    **args,
                    "revision": request.revision,
                    "decisions": [v.model_dump(mode="json") for v in values(request)],
                },
            )
            assert statement in rendering.render(cards[0])[0]
    if fallback:
        response = client.get(
            f"{demo[0]}/employees/{demo[1]['E01'].id}/change-requests?status=all",
            headers=demo[2]["admin"],
        )
        assert statement in response.text
