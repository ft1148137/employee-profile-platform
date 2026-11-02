from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, SessionDep
from app.core.security import get_password_hash, verify_password
from app.models import Message, UpdatePassword, UserPublic


class InvalidPassword(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=400, detail="当前密码不正确")


class UnchangedPassword(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=400, detail="新密码不能与当前密码相同")


router = APIRouter(prefix="/users", tags=["users"])


@router.patch("/me/password", response_model=Message)
def update_password_me(
    *, session: SessionDep, body: UpdatePassword, current_user: CurrentUser
) -> Any:
    """
    Update own password.
    """
    verified, _ = verify_password(body.current_password, current_user.hashed_password)
    if not verified:
        raise InvalidPassword()
    if body.current_password == body.new_password:
        raise UnchangedPassword()
    hashed_password = get_password_hash(body.new_password)
    current_user.hashed_password = hashed_password
    session.add(current_user)
    session.commit()
    return Message(message="密码修改成功")


@router.get("/me", response_model=UserPublic)
def read_user_me(current_user: CurrentUser) -> Any:
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
