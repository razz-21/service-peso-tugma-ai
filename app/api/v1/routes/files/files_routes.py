from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)

from app.api.deps import get_current_user, get_current_workspace_id
from app.api.v1.routes.users.users_models import User
from app.core.config import settings
from app.core.rate_limit import rate_limit

from . import files_service
from .files_schemas import FileList, FileRead

router = APIRouter()


@router.post(
    "",
    response_model=FileRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("upload", settings.RATE_LIMIT_UPLOAD_USER, by="user"))],
)
async def upload_file(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
    foreign_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
) -> FileRead:
    # Generic attachment upload: stores the bytes and links the file to the
    # owning record via `foreign_id`. Not applicant-specific — any resource can
    # reuse this endpoint.
    content_type = file.content_type or "application/octet-stream"
    if content_type not in files_service.ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type.",
        )
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="File is empty."
        )
    if len(data) > files_service.FILE_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds the 10 MB limit.",
        )
    stored = await files_service.create_file(
        foreign_id=foreign_id,
        workspace_id=workspace_id,
        filename=file.filename or "file",
        content_type=content_type,
        data=data,
        uploaded_by=current_user.id,
    )
    return FileRead.model_validate(stored)


@router.get("", response_model=FileList)
async def list_files(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    foreign_id: Annotated[UUID, Query()],
) -> FileList:
    files = await files_service.list_files(foreign_id, workspace_id=workspace_id)
    return FileList(items=[FileRead.model_validate(f) for f in files])


@router.get("/{file_id}/download")
async def download_file(
    file_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> Response:
    # Proxies the download through the API so it stays behind auth (the raw blob
    # URL is unauthenticated) and is served with the original filename.
    file = await files_service.get_file(file_id, workspace_id=workspace_id)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    data = await files_service.fetch_bytes(file)
    disposition = f"attachment; filename*=UTF-8''{quote(file.filename)}"
    return Response(
        content=data,
        media_type=file.content_type,
        headers={"Content-Disposition": disposition},
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> None:
    file = await files_service.get_file(file_id, workspace_id=workspace_id)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if not await files_service.delete_file(file):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete file",
        )
