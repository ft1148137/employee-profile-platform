from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm

from app import crud
from app.api.deps import CurrentUser, SessionDep
from app.core import security
from app.core.config import settings
from app.domains.account.limiter import check_login_limit, record_login_attempt
from app.models import Token, UserPublic

router = APIRouter(tags=["login"])


class InvalidCredentials(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=400, detail="邮箱或密码错误")


class InactiveAccount(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=400, detail="账号已停用，请联系企业管理员")


@router.post("/login/access-token")
def login_access_token(
    request: Request,
    session: SessionDep,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> Token:
    """
    OAuth2 compatible token login, get an access token for future requests
    """
    # Bound stored invalid identifiers too; valid usernames normalize to at most 510 characters.
    identifier = form_data.username.strip().lower()[:512]
    ip = request.client.host if request.client else "unknown"
    check_login_limit(session, identifier, ip)
    user = crud.authenticate(
        session=session, username=form_data.username, password=form_data.password
    )
    if not user:
        record_login_attempt(session, identifier, ip, False)
        raise InvalidCredentials()
    elif not user.is_active:
        record_login_attempt(session, identifier, ip, False)
        raise InactiveAccount()
    user.last_login_at = datetime.now(UTC)
    session.add(user)
    user_id, password_changed_at = user.id, user.password_changed_at
    record_login_attempt(session, identifier, ip, True)
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return Token(
        access_token=security.create_access_token(
            user_id,
            expires_delta=access_token_expires,
            password_changed_at=password_changed_at,
        )
    )


@router.post("/login/test-token", response_model=UserPublic)
def test_token(current_user: CurrentUser) -> Any:
    """
    Test access token
    """
    return current_user
