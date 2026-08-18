from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..jobs.jobs_models import JobStatus
from .recommended_jobs_models import RecommendationScores, RecommendedJobStatus, SkillMatch


class RecommendedJobCompany(BaseModel):
    """Company summary embedded under a recommendation's job (resolves the FK).

    `name` reads from the Company document's `company_name` attribute so read
    responses expose `{ id, name, avatar }`.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    name: str = Field(validation_alias="company_name")
    avatar: str | None = None


class RecommendedJobUser(BaseModel):
    """Assessor summary embedded under a recommendation (resolves `assessed_by`).

    `name` reads from the User document's `fullname` attribute so read responses
    expose `{ id, name, avatar }` — the officer who referred/assessed the match.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    name: str = Field(validation_alias="fullname")
    avatar: str | None = None


class RecommendedJobJob(BaseModel):
    """Embedded job summary for recommended-job read responses.

    Recommendations store only a `job_id` foreign key; read endpoints resolve it
    into this lightweight projection of the related job (see recommended_jobs_routes).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    status: JobStatus
    location: str | None = None
    salary_per_month: int | None = None
    no_of_vacancies: int = 0
    # Requirement fields, surfaced so the client can render the applicant-vs-job
    # comparison view (including the hard primary-requirement gates) without a second job
    # fetch. `sex` is emitted as its plain string value.
    skills_required: list[str] = Field(default_factory=list)
    experience_required: str | None = None
    experience_preferred: str | None = None
    minimum_education_attainment: list[str] = Field(default_factory=list)
    course_program: str | None = None
    age_range: str | None = None
    sex: str | None = None
    civil_status: list[str] = Field(default_factory=list)
    eligibility: str | None = None
    # Resolved from the job's `company_id` by the read endpoints.
    company: RecommendedJobCompany | None = None


class RecommendedJobBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    applicant_id: UUID | None = None
    scores: RecommendationScores = Field(default_factory=RecommendationScores)
    score: int = Field(default=0, ge=0, le=100)
    eligible: bool = True
    # Tri-state relevance feedback: None until assessed, then True/False.
    is_relevant: bool | None = None
    status: RecommendedJobStatus | None = None
    embedded_applicant: list[float] = Field(default_factory=list)
    embedded_job: list[float] = Field(default_factory=list)
    key_matched: list[str] = Field(default_factory=list)
    skill_matches: list[SkillMatch] = Field(default_factory=list)
    assessed_by: UUID
    workspace_id: UUID
    date_registered: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    referred_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("date_registered", "created_at", "updated_at", "referred_at", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class RecommendedJobCreate(BaseModel):
    job_id: UUID
    # Required: every new recommendation must name the applicant it is for.
    applicant_id: UUID
    scores: RecommendationScores = Field(default_factory=RecommendationScores)
    score: int = Field(default=0, ge=0, le=100)
    # Tri-state relevance feedback: None (unassessed) until an officer assesses it.
    is_relevant: bool | None = None
    status: RecommendedJobStatus | None = None
    embedded_applicant: list[float] = Field(default_factory=list)
    embedded_job: list[float] = Field(default_factory=list)
    key_matched: list[str] = Field(default_factory=list)
    # Optional: defaults to the authenticated user when omitted (see routes).
    assessed_by: UUID | None = None


class RecommendedJobPatch(BaseModel):
    job_id: UUID | None = None
    applicant_id: UUID | None = None
    scores: RecommendationScores | None = None
    score: int | None = Field(default=None, ge=0, le=100)
    is_relevant: bool | None = None
    status: RecommendedJobStatus | None = None
    embedded_applicant: list[float] | None = None
    embedded_job: list[float] | None = None
    key_matched: list[str] | None = None
    assessed_by: UUID | None = None
    # Set by the client when it advances a recommendation to `referred`, stamping
    # the referral time. Only applied when present (the patch uses exclude_unset).
    referred_at: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class RecommendedJobRead(RecommendedJobBase):
    # Read responses expose the resolved job instead of the raw foreign key:
    # `job_id` stays on the model (populated from the document) but is hidden
    # from output, while `job` carries the embedded summary.
    job_id: UUID = Field(exclude=True)
    job: RecommendedJobJob | None = None
    # Same for the assessor: the raw `assessed_by` user id is hidden, and the
    # resolved `{ id, name, avatar }` summary is exposed as `assessor`.
    assessed_by: UUID = Field(exclude=True)
    assessor: RecommendedJobUser | None = None


class RecommendedJobList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[RecommendedJobRead]


class RecommendedJobGenerate(BaseModel):
    """Request to (re)generate the Top-K job recommendations for an applicant."""

    applicant_id: UUID
    top_k: int = Field(default=5, ge=1, le=50)
