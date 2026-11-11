from uuid import UUID

from fastapi import APIRouter

from app import crud
from app.api.deps import SessionDep
from app.domains.capability.models import CapabilityProposal
from app.domains.capability.proposals import name_key
from app.domains.org.models import Employee, Organization, OrgMembership
from app.domains.setup.schemas import SetupTokenInput, SetupTokenResult
from app.domains.setup.service import issue_token
from app.models import (
    User,
    UserCreate,
    UserPublic,
)

router = APIRouter(tags=["private"], prefix="/private")


@router.post("/setup-tokens")
def create_setup_token(data: SetupTokenInput, session: SessionDep) -> SetupTokenResult:
    token = issue_token(session, data.purpose)
    session.commit()
    return SetupTokenResult(token=token)


class PrivateUserCreate(UserCreate):
    must_change_password: bool = False


@router.post("/users/", response_model=UserPublic)
def create_user(
    user_in: PrivateUserCreate, session: SessionDep, with_empty_profile: bool = False
) -> User:
    """
    Create a new user.
    """

    user = crud.create_user(session=session, user_create=user_in)
    user.must_change_password = user_in.must_change_password
    session.add(user)
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


@router.post("/capability-proposals", status_code=204)
def seed_capability_proposal(org_id: UUID, name: str, session: SessionDep) -> None:
    session.add(
        CapabilityProposal(
            org_id=org_id,
            name=name,
            name_key=name_key(name),
            category="工程",
            description="合成能力说明",
            reason="多条合成工作记录涉及这项能力",
        )
    )
    session.commit()
