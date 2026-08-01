from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.api.deps import get_current_user, get_current_workspace_id
from app.api.v1.routes.audit_logs import audit_logs_service
from app.api.v1.routes.audit_logs.audit_logs_models import AuditEntity, AuditTone
from app.api.v1.routes.users.users_models import User
from app.matching.extraction import ExtractionError, extract_text

from . import applicants_service
from .applicants_models import Applicant, ApplicantStatus
from .applicants_schemas import (
    ApplicantCreate,
    ApplicantList,
    ApplicantPatch,
    ApplicantRead,
    ResumeExtraction,
)

router = APIRouter()

# Uploaded resumes must be PDFs no larger than this.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _display_name(applicant: Applicant) -> str:
    return f"{applicant.firstname} {applicant.lastname}".strip()


async def _read_pdf_upload(file: UploadFile) -> bytes:
    """Read an uploaded file, enforcing the PDF-only / size rules.

    Rejects non-PDFs (`415`) and oversized files (`413`).
    """
    if file.content_type not in ("application/pdf", "application/x-pdf"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF files are supported.",
        )
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds the 10 MB limit.",
        )
    if not data.startswith(b"%PDF"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="The file is not a valid PDF.",
        )
    return data


@router.post("", response_model=ApplicantRead, status_code=status.HTTP_201_CREATED)
async def create_applicant(
    data: ApplicantCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> ApplicantRead:
    applicant = await applicants_service.create_applicant(
        data, created_by=current_user.id, workspace_id=workspace_id
    )
    await audit_logs_service.record_audit(
        workspace_id=workspace_id,
        entity=AuditEntity.APPLICANTS,
        entity_label="Applicant",
        actor=current_user.fullname,
        action="registered an applicant",
        icon="person_add",
        icon_tone=AuditTone.GREEN,
        chip_tone=AuditTone.GREEN,
        records=[_display_name(applicant)],
    )
    return ApplicantRead.model_validate(applicant)


@router.post("/extract", response_model=ResumeExtraction)
async def extract_applicant_resume(
    file: Annotated[UploadFile, File()],
) -> ResumeExtraction:
    # Stateless: parses the uploaded PDF and returns fields to prefill the
    # create-applicant form. Persistence happens later via `/{id}/files`.
    data = await _read_pdf_upload(file)
    try:
        return applicants_service.extract_resume(data)
    except ExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("", response_model=ApplicantList)
async def list_applicants(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query()] = None,
    status: Annotated[ApplicantStatus | None, Query()] = None,
) -> ApplicantList:
    applicants, total = await applicants_service.list_applicants(
        limit=limit, offset=offset, q=q, status=status, workspace_id=workspace_id
    )
    return ApplicantList(
        total=total,
        limit=limit,
        offset=offset,
        items=[ApplicantRead.model_validate(applicant) for applicant in applicants],
    )


@router.get("/{applicant_id}", response_model=ApplicantRead)
async def get_applicant(
    applicant_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> ApplicantRead:
    applicant = await applicants_service.get_applicant(applicant_id, workspace_id=workspace_id)
    if applicant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Applicant not found")
    return ApplicantRead.model_validate(applicant)


@router.patch("/{applicant_id}", response_model=ApplicantRead)
async def update_applicant(
    applicant_id: UUID,
    data: ApplicantPatch,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ApplicantRead:
    applicant = await applicants_service.get_applicant(applicant_id, workspace_id=workspace_id)
    if applicant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Applicant not found")
    applicant = await applicants_service.update_applicant(applicant, data)
    await audit_logs_service.record_audit(
        workspace_id=workspace_id,
        entity=AuditEntity.APPLICANTS,
        entity_label="Applicant",
        actor=current_user.fullname,
        action="updated an applicant",
        icon="person",
        icon_tone=AuditTone.GREEN,
        chip_tone=AuditTone.GREEN,
        records=[_display_name(applicant)],
    )
    return ApplicantRead.model_validate(applicant)


@router.delete("/{applicant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_applicant(
    applicant_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    applicant = await applicants_service.get_applicant(applicant_id, workspace_id=workspace_id)
    if applicant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Applicant not found")
    applicant_name = _display_name(applicant)
    if not await applicants_service.delete_applicant(applicant):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete applicant",
        )
    await audit_logs_service.record_audit(
        workspace_id=workspace_id,
        entity=AuditEntity.APPLICANTS,
        entity_label="Applicant",
        actor=current_user.fullname,
        action="deleted an applicant",
        icon="person_remove",
        icon_tone=AuditTone.RED,
        chip_tone=AuditTone.RED,
        records=[applicant_name],
    )


@router.post(
    "/{applicant_id}/files",
    response_model=ApplicantRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_applicant_file(
    applicant_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    file: Annotated[UploadFile, File()],
) -> ApplicantRead:
    # Stores the uploaded resume and persists its raw text on the applicant so
    # the matcher can embed it (the recommendation is "based on the file").
    applicant = await applicants_service.get_applicant(applicant_id, workspace_id=workspace_id)
    if applicant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Applicant not found")
    data = await _read_pdf_upload(file)
    try:
        raw_text, _ = extract_text(data)
    except ExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    applicant = await applicants_service.add_applicant_file(
        applicant,
        filename=file.filename or "resume.pdf",
        content_type=file.content_type or "application/pdf",
        data=data,
        resume_text=raw_text,
    )
    return ApplicantRead.model_validate(applicant)
