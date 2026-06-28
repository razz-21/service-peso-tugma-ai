from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.api.v1.routes.users import users_service as user_service
from app.api.v1.routes.users.users_models import User
from app.api.v1.routes.users.users_schemas import MePatch, UserRead

router = APIRouter()


@router.get("", response_model=UserRead)
async def read_me(current_user: Annotated[User, Depends(get_current_user)]) -> UserRead:
    return UserRead.model_validate(current_user)


@router.patch("", response_model=UserRead)
async def update_me(
    data: MePatch,
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserRead:
    if (
        data.email is not None
        and data.email != current_user.email
        and await user_service.get_user_by_email(data.email)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = await user_service.update_user(current_user, data)
    return UserRead.model_validate(user)
