import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.deps import get_current_user
from app.api.v1.routes.users import users_service as user_service
from app.api.v1.routes.users.users_models import User
from app.api.v1.routes.users.users_schemas import MePatch, UserRead
from app.core.blob import AVATAR_CONTENT_TYPE_EXTENSIONS, AVATAR_MAX_BYTES
from app.core.config import settings
from app.core.rate_limit import enforce, hit, parse_rate, peek, reset
from app.core.security import verify_password

logger = logging.getLogger(__name__)

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
        # Throttle current-password guesses per account: an attacker with a
        # hijacked session should not get unlimited tries at the old password.
        # Failures only — a legitimate user who mistypes a few times and then
        # succeeds has the bucket cleared below.
        pw_key = f"password:user:{current_user.id}"
        pw_limit, pw_window = parse_rate(settings.RATE_LIMIT_PASSWORD_CHANGE)
        if settings.RATE_LIMIT_ENABLED:
            try:
                enforce(await peek(pw_key, pw_limit))
            except HTTPException:
                raise
            except Exception:
                logger.exception("password-change rate limiter unavailable; failing open")
        if not verify_password(data.current_password, current_user.password):
            if settings.RATE_LIMIT_ENABLED:
                try:
                    await hit(pw_key, pw_limit, pw_window)
                except Exception:
                    logger.exception("password-change rate limiter unavailable; failing open")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect",
            )
        if settings.RATE_LIMIT_ENABLED:
            try:
                await reset(pw_key)
            except Exception:
                logger.exception("password-change rate limiter unavailable; failing open")

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


@router.delete("/avatar", response_model=UserRead)
async def remove_my_avatar(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserRead:
    # Clears the avatar URL and removes the stored Blob; a no-op when unset.
    user = await user_service.clear_user_avatar(current_user)
    return await user_service.build_user_read(user)
