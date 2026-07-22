from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from beanie import Document
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _coerce_isoformat(value: object) -> object:
    # Normalize date/datetime inputs (and BSON datetimes read back from Mongo)
    # into ISO 8601 strings; leave strings and None untouched. PyMongo cannot
    # store bare `date` objects, so every date-like field is persisted as a
    # string.
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


class Sex(StrEnum):
    MALE = "Male"
    FEMALE = "Female"


class Address(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    province: str | None = None
    municipality_city: str | None = None
    baranggay: str | None = None
    house_no_street: str | None = None


class PreferredOccupationIndustry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    occupation: str | None = None
    industry: str | None = None


class EducationalBackground(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    current_in_school: bool = False
    highest_education_level: str | None = None
    year_graduated: str | None = None
    last_attended: str | None = None
    school_university: str | None = None
    course_program: str | None = None


class Training(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    training_title: str | None = None
    duration_start: str | None = None
    duration_end: str | None = None
    institution: str | None = None
    certificate_received: str | None = None
    completed: bool = False

    @field_validator("duration_start", "duration_end", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        return _coerce_isoformat(value)


class Eligibility(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    title: str | None = None
    license_number: str | None = None
    expiry_date: str | None = None

    @field_validator("expiry_date", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        return _coerce_isoformat(value)


class WorkExperience(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    company: str | None = None
    address: str | None = None
    position: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    status_of_appointment: str | None = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        return _coerce_isoformat(value)


class Applicant(Document):
    # Application-generated UUID primary key (stored as Mongo `_id`), matching
    # this codebase's MongoDB convention (see companies/jobs models).
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]
    firstname: str
    lastname: str
    middlename: str | None = None
    suffix: str | None = None
    date_of_birth: str | None = None
    sex: Sex | None = None
    civil_status: str | None = None
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
    # Owning workspace (Workspace.id). Set from the session on creation; scopes
    # the record to a single tenant.
    workspace_id: UUID
    created_by: UUID | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("date_of_birth", "created_at", "updated_at", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        return _coerce_isoformat(value)

    class Settings:
        name = "applicants"
