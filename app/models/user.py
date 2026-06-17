from datetime import UTC, datetime

from beanie import Document, Indexed
from pydantic import EmailStr, Field


class User(Document):
    email: Indexed(EmailStr, unique=True)
    hashed_password: str
    full_name: str | None = None
    is_active: bool = True
    is_superuser: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "users"
