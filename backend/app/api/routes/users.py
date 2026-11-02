import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import col, func, select

from app import crud
from app.api.deps import (
    AuthenticatedUser,
    CurrentUser,
    SessionDep,
    get_current_active_superuser,
)
from app.core.config import settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.domains.org.models import OrgMembership, RoleGrant
from app.models import (
    Message,
    Token,
    UpdatePassword,
    User,
    UserCreate,
    UserPublic,
    UsersPublic,
    UserUpdate,
    UserUpdateMe,
)
from app.utils import generate_new_account_email, send_email

router = APIRouter(prefix="/users", tags=["users"])


class UsernameAlreadyExists(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=409, detail="登录名已存在")


class UserBelongsToOrganization(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=409, detail="User still belongs to an organization"
        )


def check_user_organization_references(session: SessionDep, user_id: uuid.UUID) -> None:
    if (
        session.exec(
            select(OrgMembership.id).where(OrgMembership.user_id == user_id)
        ).first()
        or session.exec(
            select(RoleGrant.id).where(RoleGrant.granted_by_user_id == user_id)
        ).first()
    ):
        raise UserBelongsToOrganization()


@router.get(
    "/",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UsersPublic,
)
def read_users(session: SessionDep, skip: int = 0, limit: int = 100) -> Any:
    """
    Retrieve users.
    """

    count_statement = select(func.count()).select_from(User)
    count = session.exec(count_statement).one()

    statement = (
        select(User).order_by(col(User.created_at).desc()).offset(skip).limit(limit)
    )
    users = session.exec(statement).all()

    users_public = [UserPublic.model_validate(user) for user in users]
    return UsersPublic(data=users_public, count=count)


@router.post(
    "/", dependencies=[Depends(get_current_active_superuser)], response_model=UserPublic
)
def create_user(*, session: SessionDep, user_in: UserCreate) -> Any:
    """
    Create new user.
    """
    if crud.get_user_by_username(session=session, username=user_in.username):
        raise UsernameAlreadyExists()
    user = crud.get_user_by_email(session=session, email=user_in.email)
    if user:
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system.",
        )

    user = crud.create_user(session=session, user_create=user_in)
    if settings.emails_enabled and user_in.email:
        email_data = generate_new_account_email(
            email_to=user_in.email, username=user_in.username, password=user_in.password
        )
        send_email(
            email_to=user_in.email,
            subject=email_data.subject,
            html_content=email_data.html_content,
        )
    return user


@router.patch("/me", response_model=UserPublic)
def update_user_me(
    *, session: SessionDep, user_in: UserUpdateMe, current_user: CurrentUser
) -> Any:
    """
    Update own user.
    """

    if user_in.email:
        existing_user = crud.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != current_user.id:
            raise HTTPException(
                status_code=409, detail="User with this email already exists"
            )
    user_data = user_in.model_dump(exclude_unset=True)
    current_user.sqlmodel_update(user_data)
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user


class InvalidPasswordChange(HTTPException):
    def __init__(self, message: str) -> None:
        super().__init__(status_code=400, detail=message)


@router.patch("/me/password", response_model=Token)
def update_password_me(
    *, session: SessionDep, body: UpdatePassword, current_user: AuthenticatedUser
) -> Token:
    """
    Update own password.
    """
    verified, _ = verify_password(body.current_password, current_user.hashed_password)
    if not verified:
        raise InvalidPasswordChange("当前密码不正确")
    if body.current_password == body.new_password:
        raise InvalidPasswordChange("新密码不能与当前密码相同")
    if body.new_password.strip().lower() == current_user.username.lower():
        raise InvalidPasswordChange("新密码不能与登录名相同")
    hashed_password = get_password_hash(body.new_password)
    current_user.hashed_password = hashed_password
    current_user.must_change_password = False
    current_user.password_changed_at = datetime.now(UTC)
    user_id, password_changed_at = current_user.id, current_user.password_changed_at
    session.add(current_user)
    session.commit()
    return Token(
        access_token=create_access_token(
            user_id,
            timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
            password_changed_at,
        )
    )


@router.get("/me", response_model=UserPublic)
def read_user_me(current_user: AuthenticatedUser) -> Any:
    """
    Get current user.
    """
    return current_user


@router.delete("/me", response_model=Message)
def delete_user_me(session: SessionDep, current_user: CurrentUser) -> Any:
    """
    Delete own user.
    """
    if current_user.is_superuser:
        raise HTTPException(
            status_code=403, detail="Super users are not allowed to delete themselves"
        )
    check_user_organization_references(session, current_user.id)
    session.delete(current_user)
    session.commit()
    return Message(message="User deleted successfully")


@router.get("/{user_id:uuid}", response_model=UserPublic)
def read_user_by_id(
    user_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
) -> Any:
    """
    Get a specific user by id.
    """
    user = session.get(User, user_id)
    if user == current_user:
        return user
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=403,
            detail="The user doesn't have enough privileges",
        )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch(
    "/{user_id:uuid}",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UserPublic,
)
def update_user(
    *,
    session: SessionDep,
    user_id: uuid.UUID,
    user_in: UserUpdate,
) -> Any:
    """
    Update a user.
    """

    db_user = session.get(User, user_id)
    if not db_user:
        raise HTTPException(
            status_code=404,
            detail="The user with this id does not exist in the system",
        )
    if user_in.email:
        existing_user = crud.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != user_id:
            raise HTTPException(
                status_code=409, detail="User with this email already exists"
            )

    db_user = crud.update_user(session=session, db_user=db_user, user_in=user_in)
    return db_user


@router.delete("/{user_id:uuid}", dependencies=[Depends(get_current_active_superuser)])
def delete_user(
    session: SessionDep, current_user: CurrentUser, user_id: uuid.UUID
) -> Message:
    """
    Delete a user.
    """
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user == current_user:
        raise HTTPException(
            status_code=403, detail="Super users are not allowed to delete themselves"
        )
    check_user_organization_references(session, user.id)
    session.delete(user)
    session.commit()
    return Message(message="User deleted successfully")
