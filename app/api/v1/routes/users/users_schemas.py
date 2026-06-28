from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .users_models import UserRole, UserStatus


class UserBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    fullname: str = Field(min_length=1, max_length=100)
    email: EmailStr
    role: UserRole = UserRole.OFFICER
    status: UserStatus = UserStatus.ACTIVE
    avatar: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserPatch(BaseModel):
    fullname: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: UserRole | None = None
    status: UserStatus | None = None
    avatar: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class MePatch(BaseModel):
    """Self-service profile update — intentionally excludes ``role``."""

    fullname: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    avatar: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class UserRead(UserBase):
    pass


class UserList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[UserRead]
