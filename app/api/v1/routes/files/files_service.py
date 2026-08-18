import asyncio
import contextlib
import re
from uuid import UUID, uuid4

import httpx
import vercel_blob

from app.core.blob import blob_options

from .files_models import FileObject

# Uploaded files are capped at this size (generic attachments, not just resumes).
FILE_MAX_BYTES = 10 * 1024 * 1024

# Accepted attachment types. Kept broad (documents + images) since the store is
# generic; extend as new upload surfaces need other formats.
ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/x-pdf",
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/gif",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/csv",
    }
)

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_filename(filename: str) -> str:
    """Reduce a client-supplied name to a blob-path-safe basename.

    Strips any directory components and collapses unsafe characters so the name
    can't escape the file's key prefix or break the pathname.
    """
    base = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    safe = _SAFE_FILENAME.sub("_", base).strip("._")
    return safe or "file"


async def create_file(
    *,
    foreign_id: UUID,
    workspace_id: UUID,
    filename: str,
    content_type: str,
    data: bytes,
    uploaded_by: UUID | None = None,
) -> FileObject:
    """Upload bytes to Vercel Blob and persist a `FileObject` linked to `foreign_id`.

    Stored at `files/<foreign_id>/<file_id>/<filename>` — the original name is
    kept in the key so Blob serves the right Content-Type (derived from the
    extension) and the download proxy can reuse it.
    """
    file_id = uuid4()
    safe_name = _sanitize_filename(filename)
    pathname = f"files/{foreign_id}/{file_id}/{safe_name}"
    # vercel_blob.put is synchronous (requests-based); run it off the event loop.
    result = await asyncio.to_thread(
        vercel_blob.put,
        pathname,
        data,
        blob_options(addRandomSuffix="false"),
    )
    file = FileObject(
        id=file_id,
        foreign_id=foreign_id,
        filename=safe_name,
        size=len(data),
        content_type=content_type,
        storage_ref=str(result["url"]),
        workspace_id=workspace_id,
        uploaded_by=uploaded_by,
    )
    await file.insert()
    return file


async def list_files(foreign_id: UUID, workspace_id: UUID) -> list[FileObject]:
    return (
        await FileObject.find(
            FileObject.foreign_id == foreign_id,
            FileObject.workspace_id == workspace_id,
        )
        .sort("-uploaded_at")
        .to_list()
    )


async def get_file(file_id: UUID, workspace_id: UUID) -> FileObject | None:
    # Scoped to the workspace so a file from another tenant resolves to None
    # (surfaced as a 404) rather than leaking cross-workspace data.
    return await FileObject.find_one(
        FileObject.id == file_id, FileObject.workspace_id == workspace_id
    )


async def fetch_bytes(file: FileObject) -> bytes:
    """Download the stored bytes from Blob for the authenticated download proxy."""
    async with httpx.AsyncClient() as client:
        response = await client.get(file.storage_ref)
        response.raise_for_status()
        return response.content


async def delete_file(file: FileObject) -> bool:
    # Remove the blob first (best-effort — a failed cleanup shouldn't block
    # deleting the record), then the metadata document.
    with contextlib.suppress(Exception):
        await asyncio.to_thread(vercel_blob.delete, file.storage_ref, blob_options())
    result = await file.delete()
    return result is not None and result.acknowledged


async def delete_files_for(foreign_id: UUID, workspace_id: UUID) -> None:
    """Delete every file linked to an owning record (used when it's removed)."""
    files = await list_files(foreign_id, workspace_id)
    for file in files:
        await delete_file(file)
