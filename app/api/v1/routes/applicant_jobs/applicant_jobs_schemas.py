from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.routes.recommended_jobs.recommended_jobs_models import RecommendationScores

from ..jobs.jobs_models import JobStatus
from .applicant_jobs_models import ApplicantJobStatus


class ApplicantJobJob(BaseModel):
    """Embedded job summary (resolves the stored `job_id`)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    status: JobStatus
    location: str | None = None
    salary_per_month: int | None = None


class ApplicantJobCompany(BaseModel):
    """Embedded company summary (resolves the stored `company_id`).

    `name` reads from the Company document's `company_name` attribute.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    name: str = Field(validation_alias="company_name")
    avatar: str | None = None


class ApplicantJobUser(BaseModel):
    """Embedded user summary (resolves the stored `assigned_by`).

    `name` reads from the User document's `fullname` attribute.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    name: str = Field(validation_alias="fullname")
    avatar: str | None = None


class ApplicantJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    # Read responses expose the resolved records instead of the raw foreign keys.
    job: ApplicantJobJob | None = None
    company: ApplicantJobCompany | None = None
    status: ApplicantJobStatus
    referred_on: str | None = None
    interview_on: str | None = None
    hired_on: str | None = None
    withdrawn_on: str | None = None
    match_scores: RecommendationScores = Field(default_factory=RecommendationScores)
    assigned_by: ApplicantJobUser | None = None
    created_at: str


class ApplicantJobList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ApplicantJobRead]
