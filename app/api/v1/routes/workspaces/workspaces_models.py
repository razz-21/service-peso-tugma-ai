from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from beanie import Document
from pydantic import Field, field_validator


class WorkspaceStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Workspace(Document):
    # Application-generated UUID primary key (stored as Mongo `_id`).
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]
    name: str
    description: str | None = None
    avatar: str | None = None
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
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
