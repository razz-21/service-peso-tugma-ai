from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import settings

# bcrypt only considers the first 72 bytes of the input; longer secrets raise in
# bcrypt >= 4.1, so truncate consistently for both hashing and verification.
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    secret = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(secret, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    secret = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.checkpw(secret, hashed.encode("utf-8"))


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload: dict[str, Any] = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
