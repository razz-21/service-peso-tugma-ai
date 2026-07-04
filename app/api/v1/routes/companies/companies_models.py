from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from beanie import Document
from pydantic import EmailStr, Field, field_validator


class CompanyType(StrEnum):
    SOLE_PROPRIETORSHIP = "sole_proprietorship"
    PARTNERSHIP = "partnership"
    CORPORATION = "corporation"
    COOPERATIVE = "cooperative"
    GOVERNMENT = "government"


class Company(Document):
    # Application-generated UUID primary key (stored as Mongo `_id`), replacing
    # the relational `company_id INT` with this codebase's MongoDB convention.
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]
    company_name: str
    company_type: CompanyType = CompanyType.SOLE_PROPRIETORSHIP
    email: EmailStr | None = None
    description: str | None = None
    address: str | None = None
    contact_number: str | None = None
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
        name = "companies"
