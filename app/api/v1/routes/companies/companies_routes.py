from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.api.deps import get_current_user, get_current_workspace_id, require_roles
from app.api.v1.routes.audit_logs import audit_logs_service
from app.api.v1.routes.audit_logs.audit_logs_models import AuditEntity, AuditTone
from app.api.v1.routes.users.users_models import User, UserRole
from app.core.blob import AVATAR_CONTENT_TYPE_EXTENSIONS, AVATAR_MAX_BYTES

from . import companies_service
from .companies_schemas import (
    CompanyApplicantList,
    CompanyCreate,
    CompanyList,
    CompanyPatch,
    CompanyRead,
)

router = APIRouter()


@router.post("", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
async def create_company(
    data: CompanyCreate,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CompanyRead:
    company = await companies_service.create_company(data, workspace_id=workspace_id)
    await audit_logs_service.record_audit(
        workspace_id=workspace_id,
        entity=AuditEntity.COMPANY,
        entity_label="Company",
        actor=current_user.fullname,
        action="created a company",
        icon="apartment",
        icon_tone=AuditTone.GREEN,
        chip_tone=AuditTone.GREEN,
        records=[company.company_name],
    )
    return CompanyRead.model_validate(company)


@router.get("", response_model=CompanyList)
async def list_companies(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query()] = None,
) -> CompanyList:
    companies, total = await companies_service.list_companies(
        limit=limit, offset=offset, q=q, workspace_id=workspace_id
    )
    return CompanyList(
        total=total,
        limit=limit,
        offset=offset,
        items=[CompanyRead.model_validate(company) for company in companies],
    )


@router.get("/{company_id}", response_model=CompanyRead)
async def get_company(
    company_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> CompanyRead:
    company = await companies_service.get_company(company_id, workspace_id=workspace_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return CompanyRead.model_validate(company)


@router.get("/{company_id}/applicants", response_model=CompanyApplicantList)
async def list_company_applicants(
    company_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CompanyApplicantList:
    # Applicants referred to this company's jobs. 404 first if the company is
    # missing / in another workspace, so the table never reads cross-tenant data.
    company = await companies_service.get_company(company_id, workspace_id=workspace_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    items, total = await companies_service.list_company_applicants(
        company_id=company_id, workspace_id=workspace_id, limit=limit, offset=offset
    )
    return CompanyApplicantList(total=total, limit=limit, offset=offset, items=items)


@router.patch("/{company_id}", response_model=CompanyRead)
async def update_company(
    company_id: UUID,
    data: CompanyPatch,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CompanyRead:
    company = await companies_service.get_company(company_id, workspace_id=workspace_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    company = await companies_service.update_company(company, data)
    await audit_logs_service.record_audit(
        workspace_id=workspace_id,
        entity=AuditEntity.COMPANY,
        entity_label="Company",
        actor=current_user.fullname,
        action="updated a company",
        icon="apartment",
        icon_tone=AuditTone.GREEN,
        chip_tone=AuditTone.GREEN,
        records=[company.company_name],
    )
    return CompanyRead.model_validate(company)


@router.post("/{company_id}/avatar", response_model=CompanyRead)
async def upload_company_avatar(
    company_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File()],
) -> CompanyRead:
    # Uploads the image to Vercel Blob and stores its URL on the company. Rejects
    # unsupported types (415), empty (400), and files over 5 MB (413).
    company = await companies_service.get_company(company_id, workspace_id=workspace_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    extension = AVATAR_CONTENT_TYPE_EXTENSIONS.get(file.content_type or "")
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPEG, PNG, WebP, or GIF images are supported.",
        )
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded image is empty.",
        )
    if len(data) > AVATAR_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Image exceeds the 5 MB limit.",
        )
    company = await companies_service.set_company_avatar(company, extension=extension, data=data)
    await audit_logs_service.record_audit(
        workspace_id=workspace_id,
        entity=AuditEntity.COMPANY,
        entity_label="Company",
        actor=current_user.fullname,
        action="updated a company avatar",
        icon="apartment",
        icon_tone=AuditTone.GREEN,
        chip_tone=AuditTone.GREEN,
        records=[company.company_name],
    )
    return CompanyRead.model_validate(company)


@router.delete("/{company_id}/avatar", response_model=CompanyRead)
async def remove_company_avatar(
    company_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CompanyRead:
    # Clears the avatar URL and removes the stored Blob; a no-op when unset.
    company = await companies_service.get_company(company_id, workspace_id=workspace_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    company = await companies_service.clear_company_avatar(company)
    await audit_logs_service.record_audit(
        workspace_id=workspace_id,
        entity=AuditEntity.COMPANY,
        entity_label="Company",
        actor=current_user.fullname,
        action="removed a company avatar",
        icon="apartment",
        icon_tone=AuditTone.GREEN,
        chip_tone=AuditTone.GREEN,
        records=[company.company_name],
    )
    return CompanyRead.model_validate(company)


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN))],
) -> None:
    company = await companies_service.get_company(company_id, workspace_id=workspace_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    company_name = company.company_name
    if not await companies_service.delete_company(company):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete company",
        )
    await audit_logs_service.record_audit(
        workspace_id=workspace_id,
        entity=AuditEntity.COMPANY,
        entity_label="Company",
        actor=current_user.fullname,
        action="deleted a company",
        icon="apartment",
        icon_tone=AuditTone.RED,
        chip_tone=AuditTone.RED,
        records=[company_name],
    )
