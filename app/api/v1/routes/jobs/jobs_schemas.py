from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..companies.companies_models import CompanyType
from .jobs_models import JobStatus, Sex

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
    avatar: str | None = None


class JobBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    description: str | None = None
    minimum_education_attainment: list[str] = Field(default_factory=list)
    course_program: str | None = None
    experience_required: str | None = None
    skills_required: list[str] = Field(default_factory=list)
    # Preferred (nice-to-have) requirement tier — see Job model. Defaults keep old
    # clients and pre-tiering documents working (everything stays mandatory).
    preferred_skills: list[str] = Field(default_factory=list)
    experience_preferred: str | None = None
    # Read-side bound is `ge=0`: a job whose vacancies were all consumed by
    # referrals legitimately reads as 0. Creating/patching still requires `ge=1`.
    no_of_vacancies: int = Field(default=1, ge=0, le=VACANCIES_MAX)
    salary_per_month: int | None = Field(default=None, ge=0)
    location: str | None = None
    age_range: str | None = None
    sex: Sex | None = None
    civil_status: list[str] = Field(default_factory=list)
    eligibility: str | None = None
    status: JobStatus = JobStatus.ACTIVE
    company_id: UUID
    workspace_id: UUID
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
    minimum_education_attainment: list[str] = Field(default_factory=list)
    course_program: str | None = None
    experience_required: str | None = None
    skills_required: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    experience_preferred: str | None = None
    no_of_vacancies: int = Field(default=1, ge=1, le=VACANCIES_MAX)
    salary_per_month: int | None = Field(default=None, ge=0)
    location: str | None = None
    age_range: str | None = None
    sex: Sex | None = None
    civil_status: list[str] = Field(default_factory=list)
    eligibility: str | None = None
    status: JobStatus = JobStatus.ACTIVE
    company_id: UUID


class JobPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    description: str | None = None
    minimum_education_attainment: list[str] | None = None
    course_program: str | None = None
    experience_required: str | None = None
    skills_required: list[str] | None = None
    preferred_skills: list[str] | None = None
    experience_preferred: str | None = None
    no_of_vacancies: int | None = Field(default=None, ge=1, le=VACANCIES_MAX)
    salary_per_month: int | None = Field(default=None, ge=0)
    location: str | None = None
    age_range: str | None = None
    sex: Sex | None = None
    civil_status: list[str] | None = None
    eligibility: str | None = None
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
