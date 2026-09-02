from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_workspace_id

from . import reports_service
from .reports_schemas import (
    ApplicantPlacedReport,
    ApplicantReferredReport,
    ApplicantRegisteredReport,
    EmploymentSummaryReport,
    EstablishmentsRegisteredReport,
    JobSolicitedReport,
    PesoAccomplishmentReport,
    ReferralFunnelReport,
    UnemployedByEducationReport,
)

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


@router.get("/applicant-placed", response_model=ApplicantPlacedReport)
async def get_applicant_placed(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> ApplicantPlacedReport:
    start, end = _resolve_window(start_date, end_date)
    return await reports_service.get_applicant_placed(
        workspace_id=workspace_id, start_date=start, end_date=end
    )


@router.get("/applicant-registered", response_model=ApplicantRegisteredReport)
async def get_applicant_registered(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> ApplicantRegisteredReport:
    start, end = _resolve_window(start_date, end_date)
    return await reports_service.get_applicant_registered(
        workspace_id=workspace_id, start_date=start, end_date=end
    )


@router.get("/establishments-registered", response_model=EstablishmentsRegisteredReport)
async def get_establishments_registered(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> EstablishmentsRegisteredReport:
    start, end = _resolve_window(start_date, end_date)
    return await reports_service.get_establishments_registered(
        workspace_id=workspace_id, start_date=start, end_date=end
    )


@router.get("/peso-accomplishment", response_model=PesoAccomplishmentReport)
async def get_peso_accomplishment(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> PesoAccomplishmentReport:
    start, end = _resolve_window(start_date, end_date)
    return await reports_service.get_peso_accomplishment(
        workspace_id=workspace_id, start_date=start, end_date=end
    )


@router.get("/employment-summary", response_model=EmploymentSummaryReport)
async def get_employment_summary(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> EmploymentSummaryReport:
    start, end = _resolve_window(start_date, end_date)
    return await reports_service.get_employment_summary(
        workspace_id=workspace_id, start_date=start, end_date=end
    )


@router.get("/unemployed-by-education", response_model=UnemployedByEducationReport)
async def get_unemployed_by_education(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> UnemployedByEducationReport:
    start, end = _resolve_window(start_date, end_date)
    return await reports_service.get_unemployed_by_education(
        workspace_id=workspace_id, start_date=start, end_date=end
    )


@router.get("/referral-to-placement-funnel", response_model=ReferralFunnelReport)
async def get_referral_to_placement_funnel(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> ReferralFunnelReport:
    start, end = _resolve_window(start_date, end_date)
    return await reports_service.get_referral_to_placement_funnel(
        workspace_id=workspace_id, start_date=start, end_date=end
    )
