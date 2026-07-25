from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_workspace_id

from . import reports_service
from .reports_schemas import ApplicantReferredReport, JobSolicitedReport

router = APIRouter()


def _resolve_window(start_date: date | None, end_date: date | None) -> tuple[date, date]:
    # Default window mirrors the report's "This month" preset: the current
    # calendar month up to today.
    end = end_date or datetime.now(UTC).date()
    start = start_date or end.replace(day=1)
    if start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be on or before end_date",
        )
    return start, end


@router.get("/job-solicited", response_model=JobSolicitedReport)
async def get_job_solicited(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> JobSolicitedReport:
    start, end = _resolve_window(start_date, end_date)
    return await reports_service.get_job_solicited(
        workspace_id=workspace_id, start_date=start, end_date=end
    )


@router.get("/applicant-referred", response_model=ApplicantReferredReport)
async def get_applicant_referred(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> ApplicantReferredReport:
    start, end = _resolve_window(start_date, end_date)
    return await reports_service.get_applicant_referred(
        workspace_id=workspace_id, start_date=start, end_date=end
    )
