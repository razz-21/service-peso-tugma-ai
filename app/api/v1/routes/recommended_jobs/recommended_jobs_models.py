from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from beanie import Document
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RecommendedJobStatus(StrEnum):
    REFERRED = "referred"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    # NOTE: normalized spelling of the requested "widthdrawn".
    WITHDRAWN = "withdrawn"
    NOT_HIRED = "not_hired"


class RecommendationScores(BaseModel):
    """Per-dimension match scores (0-100) produced by the recommender."""

    model_config = ConfigDict(from_attributes=True)

    semantic_similarity: int = Field(default=0, ge=0, le=100)
    skills: int = Field(default=0, ge=0, le=100)
    experience: int = Field(default=0, ge=0, le=100)
    educational_background: int = Field(default=0, ge=0, le=100)
    location_preference: int = Field(default=0, ge=0, le=100)


class RecommendedJob(Document):
    # Application-generated UUID primary key (stored as Mongo `_id`), matching
    # this codebase's MongoDB convention (see jobs/applicants models).
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]
    # Foreign key to `jobs` (Job.id). Stored as a UUID reference rather than a
    # Mongo DBRef so it round-trips like any other scalar field.
    job_id: UUID
    # Foreign key to `applicants` (Applicant.id) — the applicant this
    # recommendation is generated for. Nullable per the ERD (Table 14) so records
    # written before this field existed still load; every new record sets it
    # (enforced by RecommendedJobCreate and the route's FK check).
    applicant_id: UUID | None = None
    scores: RecommendationScores = Field(default_factory=RecommendationScores)
    # Final weighted MatchScore (0-100) produced by the recommender — the paper's
    # combined score used to rank recommendations (Table 14 `score`). Derived from
    # `scores` and the workspace weights at generation time.
    score: int = Field(default=0, ge=0, le=100)
    is_relevant: bool = False
    status: RecommendedJobStatus = RecommendedJobStatus.REFERRED
    embedded_applicant: list[float] = Field(default_factory=list)
    embedded_job: list[float] = Field(default_factory=list)
    key_matched: list[str] = Field(default_factory=list)
    # Foreign key to `users` (User.id) — the officer/user who assessed this
    # recommendation.
    assessed_by: UUID
    # Owning workspace (Workspace.id). Set from the session on creation; scopes
    # the record to a single tenant.
    workspace_id: UUID
    date_registered: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("date_registered", "created_at", "updated_at", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        # Tolerate documents whose timestamps were stored as BSON datetimes.
        if isinstance(value, datetime | date):
            return value.isoformat()
        return value

    class Settings:
        name = "recommended_jobs"
