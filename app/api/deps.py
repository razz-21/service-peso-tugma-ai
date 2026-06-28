from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.api.v1.routes.auth.auth_schemas import TokenPayload
from app.api.v1.routes.users.users_models import User
from app.core.config import settings
from app.core.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> User:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = TokenPayload(**decode_access_token(token))
    except (jwt.PyJWTError, ValueError) as exc:
        raise credentials_exc from exc

    if payload.sub is None:
        raise credentials_exc

    try:
        user_id = UUID(payload.sub)
    except ValueError as exc:
        raise credentials_exc from exc

    user = await User.get(user_id)
    if user is None:
        raise credentials_exc
    return user
