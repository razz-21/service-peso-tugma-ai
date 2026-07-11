from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from beanie import Document
from pydantic import Field, field_validator


class JobStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class Sex(StrEnum):
    FEMALE = "Female"
    MALE = "Male"
    FEMALE_MALE = "Female/Male"


class Job(Document):
    # Application-generated UUID primary key (stored as Mongo `_id`), replacing
    # the relational `job_id INT` with this codebase's MongoDB convention.
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]
    title: str
    description: str | None = None
    minimum_education_attainment: list[str] = Field(default_factory=list)
    experience_required: str | None = None
    skills_required: list[str] = Field(default_factory=list)
    no_of_vacancies: int = 0
    salary_per_month: int | None = None
    age_range: str | None = None
    sex: Sex | None = None
    civil_status: list[str] = Field(default_factory=list)
    status: JobStatus = JobStatus.ACTIVE
    # Foreign key to `companies` (Company.id). Stored as a UUID reference rather
    # than a Mongo DBRef so it round-trips like any other scalar field.
    company_id: UUID
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
        name = "jobs"
