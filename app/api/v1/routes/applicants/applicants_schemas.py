from datetime import UTC, date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .applicants_models import (
    Address,
    EducationalBackground,
    Eligibility,
    PreferredOccupationIndustry,
    Sex,
    Training,
    WorkExperience,
)

NAME_MAX = 100


def _to_isoformat(value: object) -> object:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


class ApplicantBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firstname: str = Field(min_length=1, max_length=NAME_MAX)
    lastname: str = Field(min_length=1, max_length=NAME_MAX)
    middlename: str | None = None
    suffix: str | None = None
    date_of_birth: str | None = None
    sex: Sex | None = None
    citizenship: str | None = None
    height_in_cm: float | None = None
    weight_in_kg: float | None = None
    present_address: Address = Field(default_factory=Address)
    permanent_address: Address | None = None
    primary_mobile_number: str | None = None
    secondary_mobile_number: str | None = None
    email_address: EmailStr | None = None
    employment_status: str | None = None
    preferred_occupation_industry: list[PreferredOccupationIndustry] = Field(default_factory=list)
    preferred_work_location: list[str] = Field(default_factory=list)
    salary_expectation: str | None = None
    educational_background: EducationalBackground | None = None
    trainings: list[Training] = Field(default_factory=list)
    eligibility: list[Eligibility] = Field(default_factory=list)
    work_experience: list[WorkExperience] = Field(default_factory=list)
    technical_skills: list[str] = Field(default_factory=list)
    created_by: UUID | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("date_of_birth", "created_at", "updated_at", mode="before")
    @classmethod
    def _coerce_isoformat(cls, value: object) -> object:
        return _to_isoformat(value)


class ApplicantCreate(BaseModel):
    firstname: str = Field(min_length=1, max_length=NAME_MAX)
    lastname: str = Field(min_length=1, max_length=NAME_MAX)
    middlename: str | None = None
    suffix: str | None = None
    date_of_birth: str | None = None
    sex: Sex | None = None
    citizenship: str | None = None
    height_in_cm: float | None = None
    weight_in_kg: float | None = None
    present_address: Address = Field(default_factory=Address)
    permanent_address: Address | None = None
    primary_mobile_number: str | None = None
    secondary_mobile_number: str | None = None
    email_address: EmailStr | None = None
    employment_status: str | None = None
    preferred_occupation_industry: list[PreferredOccupationIndustry] = Field(default_factory=list)
    preferred_work_location: list[str] = Field(default_factory=list)
    salary_expectation: str | None = None
    educational_background: EducationalBackground | None = None
    trainings: list[Training] = Field(default_factory=list)
    eligibility: list[Eligibility] = Field(default_factory=list)
    work_experience: list[WorkExperience] = Field(default_factory=list)
    technical_skills: list[str] = Field(default_factory=list)

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _coerce_isoformat(cls, value: object) -> object:
        return _to_isoformat(value)


class ApplicantPatch(BaseModel):
    firstname: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    lastname: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    middlename: str | None = None
    suffix: str | None = None
    date_of_birth: str | None = None
    sex: Sex | None = None
    citizenship: str | None = None
    height_in_cm: float | None = None
    weight_in_kg: float | None = None
    present_address: Address | None = None
    permanent_address: Address | None = None
    primary_mobile_number: str | None = None
    secondary_mobile_number: str | None = None
    email_address: EmailStr | None = None
    employment_status: str | None = None
    preferred_occupation_industry: list[PreferredOccupationIndustry] | None = None
    preferred_work_location: list[str] | None = None
    salary_expectation: str | None = None
    educational_background: EducationalBackground | None = None
    trainings: list[Training] | None = None
    eligibility: list[Eligibility] | None = None
    work_experience: list[WorkExperience] | None = None
    technical_skills: list[str] | None = None

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _coerce_isoformat(cls, value: object) -> object:
        return _to_isoformat(value)


class ApplicantRead(ApplicantBase):
    pass


class ApplicantList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ApplicantRead]
