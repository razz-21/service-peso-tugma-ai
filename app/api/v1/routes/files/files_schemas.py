from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FileRead(BaseModel):
    """File metadata returned to clients. The raw bytes are fetched separately
    via the download endpoint, so ``storage_ref`` is intentionally not exposed
    (the blob URL is unauthenticated; downloads go through the auth'd proxy)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    foreign_id: UUID
    filename: str
    size: int
    content_type: str
    uploaded_by: UUID | None = None
    uploaded_at: str


class FileList(BaseModel):
    items: list[FileRead]
