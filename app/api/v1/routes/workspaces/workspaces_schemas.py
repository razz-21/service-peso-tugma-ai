from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .workspaces_models import WorkspaceStatus


class WorkspaceBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    avatar: str | None = None
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    avatar: str | None = None
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE


class WorkspacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    avatar: str | None = None
    status: WorkspaceStatus | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class WorkspaceRead(WorkspaceBase):
    pass


class WorkspaceList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[WorkspaceRead]
