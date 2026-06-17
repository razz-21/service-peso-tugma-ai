from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.user import UserRead

router = APIRouter()


@router.get("/me", response_model=UserRead)
async def read_me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead(
        id=str(current_user.id),
        email=current_user.email,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
        is_superuser=current_user.is_superuser,
        created_at=current_user.created_at,
    )
