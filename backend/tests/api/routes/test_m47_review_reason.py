import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.domains.org.models import Department, ReportingLine
from app.domains.review.routing import review_assignment, reviewers_for
from app.models import User
from tests.api.routes.test_review_delegations import setup_delegation
from tests.api.routes.test_reviews import pending


@pytest.mark.parametrize(
    "route", ["admin", "direct", "department", "parent", "delegate"]
)
def test_review_assignment_reason(
    demo: tuple, db: Session, client: TestClient, route: str
) -> None:
    url, employees, auth = demo
    subject = employees["E01" if route == "admin" else "E02"]
    if route == "direct":
        db.add(
            ReportingLine(
                org_id=subject.org_id,
                employee_id=subject.id,
                manager_employee_id=employees["E01"].id,
            )
        )
    if route == "parent":
        child = Department(
            org_id=subject.org_id,
            name="合成下级部门",
            parent_department_id=subject.department_id,
        )
        db.add(child)
        db.flush()
        subject.department_id = child.id
        db.add(subject)
    if route == "delegate":
        setup_delegation(db, demo)
    db.commit()
    expected_emails = (
        ["admin@demo.example.com", settings.FIRST_SUPERUSER]
        if route == "admin"
        else ["manager.platform@demo.example.com"]
    )
    expected_reviewers = sorted(
        db.exec(select(User.id).where(User.email.in_(expected_emails))).all()
    )
    _, request = pending(
        db, demo, role="manager.platform" if route == "admin" else "employee.platform"
    )
    response = client.get(
        f"{url}/reviews/{request.id}",
        headers=auth["admin" if route in ("admin", "delegate") else "manager"],
    )
    assert response.status_code == 200, response.text
    department_name = db.exec(
        select(Department.name).where(
            Department.org_id == subject.org_id,
            Department.id == employees["E01"].department_id,
        )
    ).one()
    assert (
        response.json()["assignment_reason"]
        == {
            "admin": "申请人所在部门和上级部门都没有部门经理，由企业管理员审批",
            "direct": "申请人的直属上级，并且是该部门的部门经理",
            "department": "申请人所在部门的部门经理",
            "parent": f"申请人上级部门 {department_name} 的部门经理",
            "delegate": None,
        }[route]
    )
    if route == "delegate":
        assert response.json()["acting_for_name"]
    assert review_assignment(db, subject.org_id, subject.id)[0] == expected_reviewers
    assert reviewers_for(db, subject.org_id, subject.id) == expected_reviewers
