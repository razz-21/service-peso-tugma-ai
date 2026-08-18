from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.v1.routes.audit_logs import audit_logs_service
from app.api.v1.routes.audit_logs.audit_logs_models import AuditEntity, AuditTone
from app.api.v1.routes.users import users_service as user_service
from app.api.v1.routes.users.users_models import UserStatus
from app.api.v1.routes.users.users_schemas import UserCreate, UserRead
from app.core.security import REFRESH_TOKEN_TYPE, decode_token

from . import auth_service
from .auth_schemas import TokenPayload

router = APIRouter()


def _request_meta(request: Request) -> list[str]:
    """Client facts (IP, user agent) attached to a security audit entry."""
    ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    return [ip, user_agent]


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate) -> UserRead:
    if await user_service.get_user_by_email(data.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    user = await user_service.create_user(data)
    return await user_service.build_user_read(user)


@router.post("/login", response_model=UserRead)
async def login(
    request: Request,
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    remember_me: Annotated[bool, Form()] = False,
) -> UserRead:
    user = await user_service.authenticate(form_data.username, form_data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if user.status == UserStatus.INACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )
    auth_service.set_auth_cookies(
        response, auth_service.issue_tokens(str(user.id)), remember=remember_me
    )
    await audit_logs_service.record_audit(
        workspace_id=user.workspace_id,
        entity=AuditEntity.SECURITY,
        entity_label="Security",
        actor=user.fullname,
        action="signed in",
        icon="shield",
        icon_tone=AuditTone.GREY,
        chip_tone=AuditTone.GREY,
        meta=_request_meta(request),
    )
    return await user_service.build_user_read(user)


@router.post("/refresh", response_model=UserRead)
async def refresh(
    response: Response,
    refresh_token: Annotated[str | None, Cookie()] = None,
    pta_remember: Annotated[str | None, Cookie()] = None,
) -> UserRead:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if refresh_token is None:
        raise credentials_exc

    try:
        payload = TokenPayload(**decode_token(refresh_token))
    except (jwt.PyJWTError, ValueError) as exc:
        raise credentials_exc from exc

    if payload.sub is None or payload.type != REFRESH_TOKEN_TYPE:
        raise credentials_exc

    try:
        user_id = UUID(payload.sub)
    except ValueError as exc:
        raise credentials_exc from exc

    user = await user_service.get_user(user_id)
    if user is None:
        raise credentials_exc

    auth_service.set_auth_cookies(
        response, auth_service.issue_tokens(str(user.id)), remember=pta_remember == "1"
    )
    return await user_service.build_user_read(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    auth_service.clear_auth_cookies(response)
