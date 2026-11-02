from collections.abc import Generator
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlmodel import Session

from app.core import security
from app.core.config import settings
from app.core.db import engine
from app.models import TokenPayload, User

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token"
)


def get_db() -> Generator[Session]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_db)]
TokenDep = Annotated[str, Depends(reusable_oauth2)]


def get_authenticated_user(session: SessionDep, token: TokenDep) -> User:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except InvalidTokenError, ValidationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录",
        )
    user = session.get(User, token_data.sub)
    if not user:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用，请联系企业管理员")
    if user.password_changed_at and (
        token_data.iat < int(user.password_changed_at.timestamp())
        # A password version also revokes tokens issued within the same second.
        or token_data.password_version != user.password_changed_at.isoformat()
    ):
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return user


AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]


def get_current_user(current_user: AuthenticatedUser) -> User:
    if current_user.must_change_password:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "password_change_required",
                "message": "首次登录请先修改密码",
            },
        )
    return current_user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_active_superuser(current_user: CurrentUser) -> User:
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="权限不足，请联系企业管理员")
    return current_user
