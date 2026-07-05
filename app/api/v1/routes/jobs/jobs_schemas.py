from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..companies.companies_models import CompanyType
from .jobs_models import JobStatus

# VARCHAR/SMALLINT limits carried over from the original relational schema.
TITLE_MAX = 50
VACANCIES_MAX = 32767  # SMALLINT upper bound


class JobCompany(BaseModel):
    """Embedded company summary for job read responses.

    Jobs store only a `company_id` foreign key; read endpoints resolve it into
    this lightweight projection of the related company (see jobs_routes).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_name: str
    company_type: CompanyType


class JobBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    description: str | None = None
    minimum_education_attainment: str | None = None
    experience_required: str | None = None
    skills_required: list[str] = Field(default_factory=list)
    no_of_vacancies: int = Field(default=1, ge=1, le=VACANCIES_MAX)
    salary_per_month: int | None = Field(default=None, ge=0)
    status: JobStatus = JobStatus.ACTIVE
    company_id: UUID
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class JobCreate(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    description: str | None = None
    minimum_education_attainment: str | None = None
    experience_required: str | None = None
    skills_required: list[str] = Field(default_factory=list)
    no_of_vacancies: int = Field(default=1, ge=1, le=VACANCIES_MAX)
    salary_per_month: int | None = Field(default=None, ge=0)
    status: JobStatus = JobStatus.ACTIVE
    company_id: UUID


class JobPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    description: str | None = None
    minimum_education_attainment: str | None = None
    experience_required: str | None = None
    skills_required: list[str] | None = None
    no_of_vacancies: int | None = Field(default=None, ge=1, le=VACANCIES_MAX)
    salary_per_month: int | None = Field(default=None, ge=0)
    status: JobStatus | None = None
    company_id: UUID | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class JobRead(JobBase):
    # Read responses expose the resolved company instead of the raw foreign key:
    # `company_id` stays on the model (populated from the document) but is hidden
    # from output, while `company` carries the embedded summary.
    company_id: UUID = Field(exclude=True)
    company: JobCompany | None = None


class JobList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[JobRead]
