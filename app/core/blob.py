"""Vercel Blob helpers.

The `vercel_blob` library reads its `BLOB_READ_WRITE_TOKEN` from `os.environ`,
but this app loads configuration through pydantic-settings — `.env` is parsed
into `settings`, not exported to the process environment. So we pass the token
explicitly via the request `options`, which `vercel_blob` honors ahead of the
environment variable. When `BLOB_READ_WRITE_TOKEN` is unset in settings, the
token is omitted so the library still falls back to `os.environ`.
"""

import asyncio
import contextlib
from typing import Any
from uuid import UUID, uuid4

import vercel_blob

from app.core.config import settings

# Shared avatar-upload rules (companies, users, ...). Content types map to the
# extension Vercel Blob uses to serve the correct Content-Type (it derives the
# MIME type from the pathname).
AVATAR_MAX_BYTES = 5 * 1024 * 1024
AVATAR_CONTENT_TYPE_EXTENSIONS: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def blob_options(**extra: Any) -> dict[str, Any]:
    """Build a `vercel_blob` options dict with the configured auth token."""
    options: dict[str, Any] = dict(extra)
    if settings.BLOB_READ_WRITE_TOKEN:
        options["token"] = settings.BLOB_READ_WRITE_TOKEN
    return options


async def replace_avatar_blob(
    *, prefix: str, entity_id: UUID, extension: str, data: bytes, previous_url: str | None
) -> str:
    """Upload an avatar image and return its Blob URL, removing the previous one.

    Stored at `<prefix>/<entity_id>/avatar/<file_id>.<ext>`. `previous_url` (if
    any) is deleted best-effort after the new upload succeeds, so replaced
    images don't accumulate — a failed cleanup never fails the upload.
    """
    file_id = uuid4()
    pathname = f"{prefix}/{entity_id}/avatar/{file_id}.{extension}"
    # vercel_blob.put is synchronous (requests-based); run it off the event loop.
    result = await asyncio.to_thread(
        vercel_blob.put,
        pathname,
        data,
        blob_options(addRandomSuffix="false"),
    )
    if previous_url:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(vercel_blob.delete, previous_url, blob_options())
    return str(result["url"])
