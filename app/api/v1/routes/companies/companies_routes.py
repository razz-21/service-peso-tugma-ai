from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_workspace_id

from . import companies_service
from .companies_schemas import (
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
) -> CompanyRead:
    company = await companies_service.create_company(data, workspace_id=workspace_id)
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


@router.patch("/{company_id}", response_model=CompanyRead)
async def update_company(
    company_id: UUID,
    data: CompanyPatch,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> CompanyRead:
    company = await companies_service.get_company(company_id, workspace_id=workspace_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    company = await companies_service.update_company(company, data)
    return CompanyRead.model_validate(company)


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> None:
    company = await companies_service.get_company(company_id, workspace_id=workspace_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if not await companies_service.delete_company(company):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete company",
        )
