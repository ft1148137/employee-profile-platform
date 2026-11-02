from fastapi import APIRouter

from app import crud
from app.api.deps import SessionDep
from app.domains.org.models import Employee, Organization, OrgMembership
from app.models import (
    User,
    UserCreate,
    UserPublic,
)

router = APIRouter(tags=["private"], prefix="/private")


@router.post("/users/", response_model=UserPublic)
def create_user(
    user_in: UserCreate, session: SessionDep, with_empty_profile: bool = False
) -> User:
    """
    Create a new user.
    """

    user = crud.create_user(session=session, user_create=user_in)
    if with_empty_profile:
        org = Organization(slug=f"test-{user.id}", name="空画像测试企业")
        session.add(org)
        session.flush()
        employee = Employee(
            org_id=org.id,
            employee_code=user.username,
            display_name=user.full_name or "测试员工",
        )
        session.add(employee)
        session.flush()
        session.add(
            OrgMembership(org_id=org.id, user_id=user.id, employee_id=employee.id)
        )
        session.commit()
        session.refresh(user)
    return user
