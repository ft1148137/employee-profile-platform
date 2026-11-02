from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.domains.org.models import OrgMembership
from app.models import User
from tests.utils.org import delete_test_organization


def test_create_user(client: TestClient, db: Session) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/private/users/",
        json={
            "username": "pollo",
            "email": "pollo@listo.com",
            "password": "password123",
            "full_name": "Pollo Listo",
        },
    )

    assert r.status_code == 200

    data = r.json()

    user = db.exec(select(User).where(User.id == data["id"])).first()

    assert user
    assert user.email == "pollo@listo.com"
    assert user.full_name == "Pollo Listo"


def test_create_user_with_empty_profile(client: TestClient, db: Session) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/private/users/?with_empty_profile=true",
        json={
            "username": str(uuid4()),
            "password": "test-password-123",
            "full_name": "测试员工",
        },
    )
    assert response.status_code == 200
    user = db.get(User, UUID(response.json()["id"]))
    assert user is not None
    membership = db.exec(
        select(OrgMembership).where(OrgMembership.user_id == user.id)
    ).one()
    assert membership.employee_id is not None
    delete_test_organization(db, membership.org_id)
