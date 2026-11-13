from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.bot import schemas as bot
from app.domains.bot import tools
from app.domains.change_request import employee
from app.domains.im.rendering import render
from app.domains.org.models import ReportingLine, Role, RoleGrant
from app.models import User
from tests.api.routes.test_employee_requests import principal_for
from tests.api.routes.test_review_delegations import setup_delegation
from tests.api.routes.test_reviews import pending


@pytest.mark.parametrize("route", ["direct", "department", "delegate", "admin", "none"])
def test_request_reviewers(
    demo: tuple, db: Session, client: TestClient, route: str
) -> None:
    url, employees, auth = demo
    if route in ("direct", "delegate"):
        db.add(
            ReportingLine(
                org_id=employees["E02"].org_id,
                employee_id=employees["E02"].id,
                manager_employee_id=employees["E01"].id,
            )
        )
    if route in ("admin", "none"):
        for grant in db.exec(
            select(RoleGrant).where(
                RoleGrant.org_id == employees["E02"].org_id,
            )
        ).all():
            if route == "none" or grant.role == Role.DEPT_MANAGER:
                db.delete(grant)
    if route == "delegate":
        setup_delegation(db, demo)
    db.commit()
    owner, request = pending(db, demo)
    response = client.get(f"{url}/me/requests", headers=auth["employee"])
    public = next(
        row for row in response.json()["data"] if row["id"] == str(request.id)
    )
    reviewers = public["reviewers"]
    roles = [row["role"] for row in reviewers]
    assert (
        roles
        == {
            "direct": ["直属上级"],
            "department": ["部门经理"],
            "delegate": ["直属上级", "代理审批"],
            "admin": ["企业管理员", "企业管理员"],
            "none": [],
        }[route]
    )
    assert all(row["name"] for row in reviewers)
    assert all(set(row) == {"name", "role"} for row in reviewers)
    card = tools.status_card(bot.Request.model_validate(request))
    text, _ = render(card)
    if reviewers:
        assert all(row["name"] in text for row in reviewers)
    else:
        assert "暂时没有可用的审批人，企业管理员会处理" in text
    # The employee API and the shared profile API must agree on the projection.
    listed = employee.my_requests(owner, db).data
    assert (
        next(row for row in listed if row.id == request.id).reviewers
        == request.reviewers
    )
    for role in ("manager", "admin"):
        response = client.get(
            f"{url}/employees/{employees['E02'].id}/change-requests",
            headers=auth[role],
        )
        if response.status_code == 200:
            assert all(row["reviewers"] == [] for row in response.json()["data"])
    assert client.get(f"{url}/me/requests", headers=auth["foreign"]).status_code == 404


def test_reviewer_without_name_uses_existing_account_label(
    demo: tuple, db: Session
) -> None:
    delegation = setup_delegation(db, demo)
    delegate = db.get(User, delegation.delegate_user_id)
    assert delegate is not None
    delegate.full_name = None
    db.commit()
    _, request = pending(db, demo)
    assert request.reviewers[-1].role == "代理审批"
    assert request.reviewers[-1].name == delegate.username


def test_preview_carries_reviewers_and_other_employee_cannot_read(
    demo: tuple, db: Session, client: TestClient
) -> None:
    url, _, auth = demo
    db.add(
        ReportingLine(
            org_id=demo[1]["E02"].org_id,
            employee_id=demo[1]["E02"].id,
            manager_employee_id=demo[1]["E01"].id,
        )
    )
    db.commit()
    draft = client.post(
        f"{url}/me/requests",
        headers=auth["employee"],
        json={
            "items": [
                {
                    "op": "add",
                    "capability_key": "automated_testing",
                    "statement": "合成陈述",
                }
            ],
        },
    ).json()
    preview = client.get(
        f"{url}/me/requests/{draft['id']}/preview", headers=auth["employee"]
    ).json()
    assert preview["reviewers"] == draft["reviewers"]
    assert preview["reviewers"][0]["role"] == "直属上级"
    card = tools.preview_card(
        db, principal_for(db, demo[1]["E02"].org_id), UUID(draft["id"])
    )
    text, _ = render(card)
    assert f"提交后由 {preview['reviewers'][0]['name']}（直属上级）审批" in text
    assert (
        client.get(
            f"{url}/me/requests/{draft['id']}/preview", headers=auth["manager"]
        ).status_code
        == 404
    )
