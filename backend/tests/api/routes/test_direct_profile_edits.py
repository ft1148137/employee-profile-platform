from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.org.models import Department, Employee, OrgMembership
from app.domains.profile.models import ProfileVersion
from tests.api.routes.test_reviews import actor


def edit_body(db: Session, subject: Employee) -> dict:
    version = db.exec(
        select(ProfileVersion)
        .where(
            ProfileVersion.org_id == subject.org_id,
            ProfileVersion.subject_employee_id == subject.id,
        )
        .order_by(ProfileVersion.revision.desc())
    ).first()
    return {
        "base_revision": version.revision if version else None,
        "items": [
            {
                "op": "modify",
                "capability_key": "backend_api",
                "statement": "合成的直接修改",
                "level": "routine",
            }
        ],
    }


@pytest.mark.parametrize("suffix", ["", "/preview"])
@pytest.mark.parametrize(
    ("role", "code", "expected"),
    [
        ("manager", "E04", 404),
        ("manager", "E01", 403),
        ("employee", "E01", 404),
        ("employee", "E02", 403),
        ("foreign", "E02", 404),
    ],
)
def test_direct_route_scope(
    demo: tuple,
    db: Session,
    client: TestClient,
    suffix: str,
    role: str,
    code: str,
    expected: int,
) -> None:
    base, people, auth = demo
    subject = people[code]
    response = client.post(
        f"{base}/employees/{subject.id}/profile/direct-edits{suffix}",
        headers=auth[role],
        json=edit_body(db, subject),
    )
    assert response.status_code == expected


@pytest.mark.parametrize("role", ["admin", "manager"])
def test_preview_and_publish_http(
    demo: tuple, db: Session, client: TestClient, role: str
) -> None:
    base, people, auth = demo
    subject = people["E02"]
    body = edit_body(db, subject)
    url = f"{base}/employees/{subject.id}/profile/direct-edits"
    preview = client.post(url + "/preview", headers=auth[role], json=body)
    assert preview.status_code == 200
    assert preview.json()["revision"] == body["base_revision"]
    assert edit_body(db, subject) == body
    result = client.post(url, headers=auth[role], json=body)
    assert result.status_code == 200
    assert result.json()["revision"] == body["base_revision"] + 1
    assert result.json()["items"][0]["after"]["assessment_status"] == "confirmed"
    stale = client.post(url, headers=auth[role], json=body)
    assert stale.status_code == 409
    assert stale.json()["detail"] == "画像已变化，请重新查看"


def test_inactive_missing_foreign_and_admin_self(
    demo: tuple, db: Session, client: TestClient
) -> None:
    from app.authz.principal import Principal
    from app.domains.review.direct import (
        DirectEditInput,
        preview_direct_edit,
    )

    base, people, auth = demo
    subject = people["E02"]
    body = edit_body(db, subject)
    subject.employment_status = "inactive"
    db.commit()
    foreign = db.exec(select(Employee).where(Employee.org_id != subject.org_id)).first()
    assert foreign is not None
    for employee_id in (subject.id, foreign.id, uuid4()):
        response = client.post(
            f"{base}/employees/{employee_id}/profile/direct-edits",
            headers=auth["admin"],
            json=body,
        )
        assert response.status_code == 404
    admin = actor(db, subject.org_id, "admin@demo.example.com")
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == subject.org_id,
            OrgMembership.user_id == admin.user_id,
        )
    ).one()
    membership.employee_id = people["E04"].id
    db.commit()
    response = client.post(
        f"{base}/employees/{people['E04'].id}/profile/direct-edits",
        headers=auth["admin"],
        json=body,
    )
    assert response.status_code == 403
    worker = Principal.system_worker(subject.org_id)
    with pytest.raises(HTTPException) as error:
        preview_direct_edit(
            worker, db, subject.id, DirectEditInput.model_validate(body)
        )
    assert error.value.status_code == 404


def test_manager_can_edit_descendant_department(
    demo: tuple, db: Session, client: TestClient
) -> None:
    base, people, auth = demo
    subject = people["E02"]
    child = Department(
        org_id=subject.org_id,
        parent_department_id=subject.department_id,
        name="合成下级部门",
    )
    db.add(child)
    db.flush()
    subject.department_id = child.id
    db.commit()
    response = client.post(
        f"{base}/employees/{subject.id}/profile/direct-edits",
        headers=auth["manager"],
        json=edit_body(db, subject),
    )
    assert response.status_code == 200
