from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.deps import get_current_user
from app.api.v1.routes.users import users_service as user_service
from app.api.v1.routes.users.users_models import User
from app.api.v1.routes.users.users_schemas import MePatch, UserRead
from app.core.blob import AVATAR_CONTENT_TYPE_EXTENSIONS, AVATAR_MAX_BYTES
from app.core.security import verify_password

router = APIRouter()


@router.get("", response_model=UserRead)
async def read_me(current_user: Annotated[User, Depends(get_current_user)]) -> UserRead:
    return await user_service.build_user_read(current_user)


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

    # Changing the password requires proving knowledge of the current one.
    if data.password is not None:
        if not data.current_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is required to change your password",
            )
        if not verify_password(data.current_password, current_user.password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect",
            )

    user = await user_service.update_user(current_user, data)
    return await user_service.build_user_read(user)


@router.post("/avatar", response_model=UserRead)
async def upload_my_avatar(
    current_user: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File()],
) -> UserRead:
    # Uploads the image to Vercel Blob and stores its URL on the current user.
    # Rejects unsupported types (415), empty (400), and files over 5 MB (413).
    extension = AVATAR_CONTENT_TYPE_EXTENSIONS.get(file.content_type or "")
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPEG, PNG, WebP, or GIF images are supported.",
        )
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded image is empty.",
        )
    if len(data) > AVATAR_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Image exceeds the 5 MB limit.",
        )
    user = await user_service.set_user_avatar(current_user, extension=extension, data=data)
    return await user_service.build_user_read(user)
