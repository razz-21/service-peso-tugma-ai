from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .companies_models import CompanyType

# VARCHAR limits carried over from the original relational schema.
COMPANY_NAME_MAX = 30
CONTACT_NUMBER_MAX = 13


class CompanyBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_name: str = Field(min_length=1, max_length=COMPANY_NAME_MAX)
    company_type: CompanyType = CompanyType.SOLE_PROPRIETORSHIP
    email: EmailStr | None = None
    description: str | None = None
    address: str | None = None
    contact_number: str | None = Field(default=None, max_length=CONTACT_NUMBER_MAX)
    avatar: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class CompanyCreate(BaseModel):
    company_name: str = Field(min_length=1, max_length=COMPANY_NAME_MAX)
    company_type: CompanyType = CompanyType.SOLE_PROPRIETORSHIP
    email: EmailStr | None = None
    description: str | None = None
    address: str | None = None
    contact_number: str | None = Field(default=None, max_length=CONTACT_NUMBER_MAX)
    avatar: str | None = None


class CompanyPatch(BaseModel):
    company_name: str | None = Field(default=None, min_length=1, max_length=COMPANY_NAME_MAX)
    company_type: CompanyType | None = None
    email: EmailStr | None = None
    description: str | None = None
    address: str | None = None
    contact_number: str | None = Field(default=None, max_length=CONTACT_NUMBER_MAX)
    avatar: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class CompanyRead(CompanyBase):
    pass


class CompanyList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CompanyRead]
