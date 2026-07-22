from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..jobs.jobs_models import JobStatus
from .recommended_jobs_models import RecommendationScores, RecommendedJobStatus


class RecommendedJobJob(BaseModel):
    """Embedded job summary for recommended-job read responses.

    Recommendations store only a `job_id` foreign key; read endpoints resolve it
    into this lightweight projection of the related job (see recommended_jobs_routes).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    status: JobStatus


class RecommendedJobBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    applicant_id: UUID | None = None
    scores: RecommendationScores = Field(default_factory=RecommendationScores)
    is_relevant: bool = False
    status: RecommendedJobStatus = RecommendedJobStatus.REFERRED
    embedded_applicant: list[float] = Field(default_factory=list)
    embedded_job: list[float] = Field(default_factory=list)
    key_matched: list[str] = Field(default_factory=list)
    assessed_by: UUID
    workspace_id: UUID
    date_registered: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("date_registered", "created_at", "updated_at", mode="before")
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
    is_relevant: bool = False
    status: RecommendedJobStatus = RecommendedJobStatus.REFERRED
    embedded_applicant: list[float] = Field(default_factory=list)
    embedded_job: list[float] = Field(default_factory=list)
    key_matched: list[str] = Field(default_factory=list)
    # Optional: defaults to the authenticated user when omitted (see routes).
    assessed_by: UUID | None = None


class RecommendedJobPatch(BaseModel):
    job_id: UUID | None = None
    applicant_id: UUID | None = None
    scores: RecommendationScores | None = None
    is_relevant: bool | None = None
    status: RecommendedJobStatus | None = None
    embedded_applicant: list[float] | None = None
    embedded_job: list[float] | None = None
    key_matched: list[str] | None = None
    assessed_by: UUID | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class RecommendedJobRead(RecommendedJobBase):
    # Read responses expose the resolved job instead of the raw foreign key:
    # `job_id` stays on the model (populated from the document) but is hidden
    # from output, while `job` carries the embedded summary.
    job_id: UUID = Field(exclude=True)
    job: RecommendedJobJob | None = None


class RecommendedJobList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[RecommendedJobRead]
