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
    course_program: str | None = None
    experience_required: str | None = None
    skills_required: list[str] = Field(default_factory=list)
    # Requirement tiering (additive, backward-compatible): the fields above are the
    # mandatory (must-have) tier; these are the preferred (nice-to-have) tier the
    # scorer treats as a bounded bonus. Empty / False on documents written before
    # tiering existed, so every existing requirement stays fully mandatory.
    preferred_skills: list[str] = Field(default_factory=list)
    preferred_education: list[str] = Field(default_factory=list)
    # When True, `experience_required` is a preferred (bonus-only) constraint: an
    # unmet experience requirement adds no penalty. When False it is mandatory.
    experience_is_preferred: bool = False
    no_of_vacancies: int = 0
    salary_per_month: int | None = None
    # Work location / address of the job. Used by the matching pipeline's
    # location-preference score (applicant preferred locations vs this value).
    location: str | None = None
    age_range: str | None = None
    sex: Sex | None = None
    civil_status: list[str] = Field(default_factory=list)
    eligibility: str | None = None
    status: JobStatus = JobStatus.ACTIVE
    # Cached semantic embedding of the job's text (title, description, required
    # skills/experience/education), produced by the matching pipeline's embedding
    # model. Persisted so the Top-N recommender doesn't re-embed every job on each
    # run; recompute when the job's text fields change. Empty until first computed.
    # Kept off the read/write schemas (JobCreate/JobPatch/JobRead) so it stays an
    # internal cache rather than a client-facing field.
    embedding: list[float] = Field(default_factory=list)
    # Fingerprint (hash) of the job text that produced `embedding`. The recommender
    # compares it against the current text on every run and re-embeds when they
    # differ, so a job whose requirements were edited gets a fresh vector instead
    # of scoring against a stale cache. Empty until the embedding is first computed.
    embedding_source: str = Field(default="")
    # Foreign key to `companies` (Company.id). Stored as a UUID reference rather
    # than a Mongo DBRef so it round-trips like any other scalar field.
    company_id: UUID
    # Owning workspace (Workspace.id). Set from the session on creation; scopes
    # the record to a single tenant.
    workspace_id: UUID
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
