import logging
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
from app.core.config import settings
from app.core.rate_limit import (
    client_ip,
    email_key,
    enforce,
    hit,
    parse_rate,
    peek,
    rate_limit,
    reset,
)
from app.core.security import REFRESH_TOKEN_TYPE, decode_token

from . import auth_service
from .auth_schemas import TokenPayload

logger = logging.getLogger(__name__)

router = APIRouter()


def _request_meta(request: Request) -> list[str]:
    """Client facts (IP, user agent) attached to a security audit entry."""
    # Resolve the real caller IP behind Vercel's proxy; `request.client.host`
    # would record the edge proxy's address on every sign-in.
    return [client_ip(request), request.headers.get("user-agent", "unknown")]


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("register", settings.RATE_LIMIT_REGISTER_IP))],
)
async def register(data: UserCreate) -> UserRead:
    if await user_service.get_user_by_email(data.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    user = await user_service.create_user(data)
    return await user_service.build_user_read(user)


@router.post(
    "/login",
    response_model=UserRead,
    # IP-keyed limiter: consumed on every attempt (success or failure) as a
    # blanket flood defence. The per-account (email) limiter is applied inside
    # the handler because it counts failures only (see below).
    dependencies=[Depends(rate_limit("login", settings.RATE_LIMIT_LOGIN_IP))],
)
async def login(
    request: Request,
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    remember_me: Annotated[bool, Form()] = False,
) -> UserRead:
    # Per-account throttle: check (read-only) before authenticating so a
    # rotating-IP attacker can't grind one account, but only *increment* on a
    # failed attempt and *clear* on success — otherwise a legitimate user signing
    # in repeatedly (multiple devices, flaky network) would lock themselves out.
    key = email_key(form_data.username)
    email_limit, email_window = parse_rate(settings.RATE_LIMIT_LOGIN_EMAIL)
    if settings.RATE_LIMIT_ENABLED:
        try:
            enforce(await peek(key, email_limit))
        except HTTPException:
            raise
        except Exception:
            # Fail open on store errors, matching the audit-log best-effort
            # pattern; authenticate() can't reach Mongo either in that case.
            logger.exception("login rate limiter unavailable; failing open")

    user = await user_service.authenticate(form_data.username, form_data.password)
    if user is None:
        if settings.RATE_LIMIT_ENABLED:
            try:
                result = await hit(key, email_limit, email_window)
                # Audit exactly once, on the failure that fills the bucket — the
                # next attempt for this account will be blocked. Writing on every
                # blocked request would turn the limiter into a log-amplification
                # vector. Do not include the attempted email: it would land
                # unhashed in a readable log for an account that may not exist.
                if result.count == email_limit:
                    await audit_logs_service.record_audit(
                        workspace_id=None,
                        entity=AuditEntity.SECURITY,
                        entity_label="Security",
                        actor="System",
                        action="blocked repeated sign-in attempts from",
                        icon="shield",
                        icon_tone=AuditTone.RED,
                        chip_tone=AuditTone.RED,
                        meta=_request_meta(request),
                    )
            except Exception:
                logger.exception("login rate limiter unavailable; failing open")
        # Same generic 401 for both keys — a "too many attempts for this account"
        # detail would reintroduce account enumeration on an unauthenticated route.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    # A correct password forgives past failures for this account.
    if settings.RATE_LIMIT_ENABLED:
        try:
            await reset(key)
        except Exception:
            logger.exception("login rate limiter unavailable; failing open")
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


@router.post(
    "/refresh",
    response_model=UserRead,
    dependencies=[Depends(rate_limit("refresh", settings.RATE_LIMIT_REFRESH_IP))],
)
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
