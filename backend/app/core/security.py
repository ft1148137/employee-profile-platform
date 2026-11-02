from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.config import settings

password_hash = PasswordHash(
    (
        Argon2Hasher(),
        BcryptHasher(),
    )
)


ALGORITHM = "HS256"


def create_access_token(
    subject: str | Any,
    expires_delta: timedelta,
    password_changed_at: datetime | None = None,
) -> str:
    expire = datetime.now(UTC) + expires_delta
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "iat": datetime.now(UTC),
        "password_version": password_changed_at.isoformat()
        if password_changed_at
        else None,
    }
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_password(
    plain_password: str, hashed_password: str
) -> tuple[bool, str | None]:
    return password_hash.verify_and_update(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return password_hash.hash(password)
