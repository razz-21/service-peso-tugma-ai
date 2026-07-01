from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.api.v1.routes.auth.auth_schemas import TokenPayload
from app.api.v1.routes.users.users_models import User
from app.core.config import settings
from app.core.security import ACCESS_COOKIE_NAME, ACCESS_TOKEN_TYPE, decode_token

# auto_error=False so the header is optional — the httpOnly cookie is the primary
# source in the browser; the Bearer header remains a fallback for API clients.
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_PREFIX}/auth/login", auto_error=False
)


async def get_current_user(
    access_token: Annotated[str | None, Cookie(alias=ACCESS_COOKIE_NAME)] = None,
    header_token: Annotated[str | None, Depends(oauth2_scheme)] = None,
) -> User:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = access_token or header_token
    if token is None:
        raise credentials_exc

    try:
        payload = TokenPayload(**decode_token(token))
    except (jwt.PyJWTError, ValueError) as exc:
        raise credentials_exc from exc

    if payload.sub is None or payload.type != ACCESS_TOKEN_TYPE:
        raise credentials_exc

    try:
        user_id = UUID(payload.sub)
    except ValueError as exc:
        raise credentials_exc from exc

    user = await User.get(user_id)
    if user is None:
        raise credentials_exc
    return user
