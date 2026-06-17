import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.config import settings
from app.core.security import decode_access_token
from app.models.user import User
from app.schemas.auth import TokenPayload

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
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

    user = await User.get(payload.sub)
    if user is None or not user.is_active:
        raise credentials_exc
    return user
