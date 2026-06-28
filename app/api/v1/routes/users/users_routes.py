from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from . import users_service
from .users_schemas import UserCreate, UserList, UserPatch, UserRead

router = APIRouter()


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(data: UserCreate) -> UserRead:
    if await users_service.get_user_by_email(data.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    user = await users_service.create_user(data)
    return UserRead.model_validate(user)


@router.get("", response_model=UserList)
async def list_users(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserList:
    users, total = await users_service.list_users(limit=limit, offset=offset)
    return UserList(
        total=total,
        limit=limit,
        offset=offset,
        items=[UserRead.model_validate(user) for user in users],
    )


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: UUID) -> UserRead:
    user = await users_service.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UserRead.model_validate(user)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(user_id: UUID, data: UserPatch) -> UserRead:
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
    return UserRead.model_validate(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: UUID) -> None:
    user = await users_service.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await users_service.delete_user(user)
