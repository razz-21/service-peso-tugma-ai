from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from beanie import Document
from pydantic import Field, field_validator

# Reuse the shared per-dimension score value object from the recommender.
from app.api.v1.routes.recommended_jobs.recommended_jobs_models import RecommendationScores


class ApplicantJobStatus(StrEnum):
    REFERRED = "referred"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    HIRED = "hired"
    WITHDRAWN = "withdrawn"
    NOT_HIRED = "not_hired"


class ApplicantJob(Document):
    # Application-generated UUID primary key (stored as Mongo `_id`), matching
    # this codebase's MongoDB convention (see jobs/applicants models).
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]
    # Foreign key to `jobs` (Job.id).
    job_id: UUID
    # Foreign key to `companies` (Company.id).
    company_id: UUID
    status: ApplicantJobStatus = ApplicantJobStatus.REFERRED
    # ISO-8601 timestamps for each lifecycle transition; null until it happens.
    referred_on: str | None = None
    interview_on: str | None = None
    hired_on: str | None = None
    withdrawn_on: str | None = None
    # Per-dimension match scores captured for this applicant-job pairing.
    match_scores: RecommendationScores = Field(default_factory=RecommendationScores)
    # Foreign key to `users` (User.id) — the officer/user who assigned this.
    assigned_by: UUID
    # Owning workspace (Workspace.id). Set from the session; scopes the record to
    # a single tenant. Kept off the read schema (internal scoping only).
    workspace_id: UUID
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator(
        "referred_on",
        "interview_on",
        "hired_on",
        "withdrawn_on",
        "created_at",
        mode="before",
    )
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        # Tolerate documents whose timestamps were stored as BSON datetimes.
        if isinstance(value, datetime | date):
            return value.isoformat()
        return value

    class Settings:
        name = "applicant_jobs"
