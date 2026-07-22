from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user, get_current_workspace_id
from app.api.v1.routes.users.users_models import User

from . import applicants_service
from .applicants_schemas import (
    ApplicantCreate,
    ApplicantList,
    ApplicantPatch,
    ApplicantRead,
)

router = APIRouter()


@router.post("", response_model=ApplicantRead, status_code=status.HTTP_201_CREATED)
async def create_applicant(
    data: ApplicantCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> ApplicantRead:
    applicant = await applicants_service.create_applicant(
        data, created_by=current_user.id, workspace_id=workspace_id
    )
    return ApplicantRead.model_validate(applicant)


@router.get("", response_model=ApplicantList)
async def list_applicants(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query()] = None,
) -> ApplicantList:
    applicants, total = await applicants_service.list_applicants(
        limit=limit, offset=offset, q=q, workspace_id=workspace_id
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
) -> ApplicantRead:
    applicant = await applicants_service.get_applicant(applicant_id, workspace_id=workspace_id)
    if applicant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Applicant not found")
    applicant = await applicants_service.update_applicant(applicant, data)
    return ApplicantRead.model_validate(applicant)


@router.delete("/{applicant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_applicant(
    applicant_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> None:
    applicant = await applicants_service.get_applicant(applicant_id, workspace_id=workspace_id)
    if applicant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Applicant not found")
    if not await applicants_service.delete_applicant(applicant):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete applicant",
        )
