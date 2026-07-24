from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user, get_current_workspace_id, require_roles

from . import users_service
from .users_models import User, UserRole
from .users_schemas import UserCreate, UserList, UserPatch, UserRead

router = APIRouter()


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreate,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    # Officers may view members but not invite new ones — super admins and admins can.
    _: Annotated[User, Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN))],
) -> UserRead:
    if await users_service.get_user_by_email(data.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    user = await users_service.create_user(data, workspace_id=workspace_id)
    return await users_service.build_user_read(user)


@router.get("", response_model=UserList)
async def list_users(
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str, Query()] = None,
    role: Annotated[str, Query()] = None,
    workspace_id: Annotated[UUID | None, Query()] = None,
) -> UserList:
    # Officers can't browse User Management; the only user list they may read is
    # their own workspace's members. Pin the filter to their workspace so they
    # can't enumerate users elsewhere (or globally).
    if current_user.role == UserRole.OFFICER:
        workspace_id = current_user.workspace_id
    users, total = await users_service.list_users(
        limit=limit, offset=offset, q=q, role=role, workspace_id=workspace_id
    )
    return UserList(
        total=total,
        limit=limit,
        offset=offset,
        items=users,
    )


# User Management is off-limits to officers — viewing a single user's details
# and modifying/removing users are super-admin/admin actions.
_manage_users = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN))


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: UUID, _: Annotated[User, _manage_users]) -> UserRead:
    user = await users_service.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return await users_service.build_user_read(user)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: UUID, data: UserPatch, _: Annotated[User, _manage_users]
) -> UserRead:
    user = await users_service.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if (
        data.email is not None
        and data.email != user.email
        and await users_service.get_user_by_email(data.email)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = await users_service.update_user(user, data)
    return await users_service.build_user_read(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: UUID, _: Annotated[User, _manage_users]) -> None:
    user = await users_service.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not await users_service.delete_user(user):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete user"
        )
