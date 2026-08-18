from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from beanie import Document
from pydantic import Field, field_validator


def _coerce_isoformat(value: object) -> object:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


class FileObject(Document):
    """A stored file, kept in its own collection and linked to an owning record
    by ``foreign_id`` rather than embedded in that record.

    Generic on purpose: ``foreign_id`` is the id of whatever entity the file
    belongs to (an applicant, a company, ...) so the same collection backs file
    attachments across the app. The raw bytes live in Vercel Blob at
    ``storage_ref``; only metadata is stored here.
    """

    # Application-generated UUID primary key (stored as Mongo `_id`), matching
    # this codebase's MongoDB convention.
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]
    # Id of the owning record (e.g. an Applicant.id). Not a DB-enforced foreign
    # key — Mongo has none — but the logical link used to list an entity's files.
    foreign_id: UUID
    filename: str
    size: int
    content_type: str
    # Vercel Blob URL the bytes were uploaded to.
    storage_ref: str
    # Owning workspace (Workspace.id). Scopes the record to a single tenant.
    workspace_id: UUID
    uploaded_by: UUID | None = None
    uploaded_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("uploaded_at", mode="before")
    @classmethod
    def _to_isoformat(cls, value: object) -> object:
        return _coerce_isoformat(value)

    class Settings:
        name = "files"
        indexes = ["foreign_id", "workspace_id"]
