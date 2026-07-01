from fastapi import Response

from app.core.config import settings
from app.core.security import (
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    REMEMBER_COOKIE_NAME,
    create_access_token,
    create_refresh_token,
)

from .auth_schemas import Token

# The refresh token (and remember flag) are only ever needed by the auth
# endpoints, so scope their cookies to that path instead of the whole site.
REFRESH_COOKIE_PATH = f"{settings.API_V1_PREFIX}/auth"

_ACCESS_MAX_AGE = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
_REFRESH_MAX_AGE = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400


def issue_tokens(subject: str) -> Token:
    return Token(
        access_token=create_access_token(subject),
        refresh_token=create_refresh_token(subject),
    )


def set_auth_cookies(response: Response, tokens: Token, *, remember: bool) -> None:
    """Write the token pair as httpOnly cookies.

    When ``remember`` is False the cookies are session cookies (``max_age=None``)
    so they are dropped when the browser closes; otherwise they persist up to the
    token lifetimes. A server-only ``remember`` flag is stored so ``/auth/refresh``
    can preserve this choice.
    """
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        tokens.access_token,
        max_age=_ACCESS_MAX_AGE if remember else None,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        tokens.refresh_token,
        max_age=_REFRESH_MAX_AGE if remember else None,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN,
        path=REFRESH_COOKIE_PATH,
    )
    response.set_cookie(
        REMEMBER_COOKIE_NAME,
        "1" if remember else "0",
        max_age=_REFRESH_MAX_AGE if remember else None,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN,
        path=REFRESH_COOKIE_PATH,
    )


def clear_auth_cookies(response: Response) -> None:
    def _delete(name: str, path: str) -> None:
        response.delete_cookie(
            name,
            path=path,
            domain=settings.COOKIE_DOMAIN,
            secure=settings.COOKIE_SECURE,
            samesite=settings.COOKIE_SAMESITE,
            httponly=True,
        )

    _delete(ACCESS_COOKIE_NAME, "/")
    _delete(REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH)
    _delete(REMEMBER_COOKIE_NAME, REFRESH_COOKIE_PATH)
