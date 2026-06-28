from datetime import UTC, datetime
from uuid import UUID

from app.core.security import hash_password, verify_password

from .users_models import User
from .users_schemas import MePatch, UserCreate, UserPatch


async def get_user(user_id: UUID) -> User | None:
    return await User.get(user_id)


async def get_user_by_email(email: str) -> User | None:
    return await User.find_one(User.email == email)


async def create_user(data: UserCreate) -> User:
    user = User(
        id=data.id,
        fullname=data.fullname,
        email=data.email,
        password=hash_password(data.password),
        role=data.role,
        avatar=data.avatar,
    )
    await user.insert()
    return user


async def list_users(limit: int, offset: int) -> tuple[list[User], int]:
    total = await User.count()
    users = await User.find_all().sort("-created_at").skip(offset).limit(limit).to_list()
    return users, total


async def update_user(user: User, data: UserPatch | MePatch) -> User:
    changes = data.model_dump(exclude_unset=True)
    password = changes.pop("password", None)
    if password is not None:
        user.password = hash_password(password)
    for field, value in changes.items():
        setattr(user, field, value)
    user.updated_at = datetime.now(UTC)
    await user.save()
    return user


async def delete_user(user: User) -> None:
    await user.delete()


async def authenticate(email: str, password: str) -> User | None:
    user = await get_user_by_email(email)
    if user is None or not verify_password(password, user.password):
        return None
    return user
