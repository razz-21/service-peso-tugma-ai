from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from beanie import Document
from pydantic import BaseModel, Field, field_validator


class WorkspaceStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


# A workspace key is a slug derived from the name: lowercase alphanumerics
# grouped by single underscores (e.g. "Workspace 1" -> "workspace_1").
WORKSPACE_KEY_PATTERN = r"^[a-z0-9]+(?:_[a-z0-9]+)*$"


class MatchingScore(BaseModel):
    """Per-workspace weights (in percent) controlling how job matches are scored.

    The five factors are expected to total 100%. Defaults follow the standard
    scoring profile: semantic 50, skills 20, experience 15, education 10, location 5.
    """

    semantic_similarity: float = Field(default=50, ge=0, le=100)
    skills_match: float = Field(default=20, ge=0, le=100)
    experience_match: float = Field(default=15, ge=0, le=100)
    educational_match: float = Field(default=10, ge=0, le=100)
    location_preference: float = Field(default=5, ge=0, le=100)


class Workspace(Document):
    # Application-generated UUID primary key (stored as Mongo `_id`).
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]
    name: str
    # Defaulted so documents created before `key` existed still load.
    key: str = ""
    description: str | None = None
    avatar: str | None = None
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
    # Defaulted so documents created before `matching_score` existed still load.
    matching_score: MatchingScore = Field(default_factory=MatchingScore)
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
        name = "workspaces"
