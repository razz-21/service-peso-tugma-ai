from app.core.security import hash_password, verify_password
from app.models.user import User
from app.schemas.user import UserCreate


async def get_user_by_email(email: str) -> User | None:
    return await User.find_one(User.email == email)


async def create_user(data: UserCreate) -> User:
    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
    )
    await user.insert()
    return user


async def authenticate(email: str, password: str) -> User | None:
    user = await get_user_by_email(email)
    if user is None or not verify_password(password, user.hashed_password):
        return None
    return user
