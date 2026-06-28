from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from beanie import Document, Indexed
from pydantic import EmailStr, Field, field_validator


class UserRole(StrEnum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    OFFICER = "officer"


class UserStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class User(Document):
    # Application-generated UUID primary key (stored as Mongo `_id`).
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]
    fullname: str
    email: Indexed(EmailStr, unique=True)  # type: ignore[valid-type]
    password: str
    role: UserRole = UserRole.OFFICER
    status: UserStatus = UserStatus.ACTIVE
    avatar: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        # Tolerate documents whose timestamps were stored as BSON datetimes.
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    class Settings:
        name = "users"
