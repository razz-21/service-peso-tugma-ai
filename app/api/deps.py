from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

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
        detail="Invalid authentication credentials",
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


async def get_current_workspace_id(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UUID:
    """Resolve the active workspace from the authenticated session.

    Acts as the workspace guard for tenant-scoped routes: creation endpoints for
    applicants/companies/jobs/users depend on this so a caller whose session has no
    workspace selected is rejected up front rather than persisting orphaned data.
    """
    if current_user.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace id is required",
        )
    return current_user.workspace_id


def require_roles(*allowed_roles: UserRole) -> Callable[[User], Awaitable[User]]:
    """Build a dependency that admits only the given roles, else 403.

    Usage: ``Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN))``. The
    resolved ``User`` is returned so handlers can reuse it.
    """

    async def dependency(current_user: Annotated[User, Depends(get_current_user)]) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return dependency


def authorize_workspace_access(current_user: User, workspace_id: UUID) -> None:
    """Confine non-super-admins to the workspace they belong to.

    Super admins may act on any workspace; every other role is limited to their
    own. Raises 403 when an admin/officer targets a different workspace.
    """
    if current_user.role == UserRole.SUPER_ADMIN:
        return
    if current_user.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this workspace",
        )


# Imported at the bottom to break a circular import: this shared ``deps`` module is
# pulled in by the users/auth route packages, whose ``__init__`` eagerly import their
# routers — and ``users_routes`` imports ``get_current_workspace_id`` from here. Defining
# the dependencies above first guarantees that name exists when the cycle re-enters this
# module. ``from __future__ import annotations`` keeps the annotations above lazy so the
# functions can be defined before these names are bound.
from app.api.v1.routes.auth.auth_schemas import TokenPayload  # noqa: E402
from app.api.v1.routes.users.users_models import User, UserRole  # noqa: E402
