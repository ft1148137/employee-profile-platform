from typing import Any

from fastapi import APIRouter

from app import crud
from app.api.deps import SessionDep
from app.models import (
    UserCreate,
    UserPublic,
)

router = APIRouter(tags=["private"], prefix="/private")


class PrivateUserCreate(UserCreate):
    must_change_password: bool = False


@router.post("/users/", response_model=UserPublic)
def create_user(user_in: PrivateUserCreate, session: SessionDep) -> Any:
    """
    Create a new user.
    """

    user = crud.create_user(session=session, user_create=user_in)
    user.must_change_password = user_in.must_change_password
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
