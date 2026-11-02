from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import AuthenticatedUser, SessionDep
from app.core.config import settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.models import Token, UpdatePassword, UserPublic

router = APIRouter(prefix="/users", tags=["users"])


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


class UserEndpointNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=404, detail="请求的接口不存在")


@router.api_route(
    "/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"], include_in_schema=False
)
def removed_user_endpoint() -> None:
    raise UserEndpointNotFound()
