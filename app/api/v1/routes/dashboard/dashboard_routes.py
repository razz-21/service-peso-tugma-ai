from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_workspace_id

from . import dashboard_service
from .dashboard_schemas import (
    DashboardActivity,
    DashboardSummary,
    MatchingFunnel,
    PlacementsOverTime,
)

router = APIRouter()

# Default window mirrors the dashboard's date picker: the trailing 30 days ending
# today (e.g. "Jun 24, 2026 – Jul 24, 2026").
_DEFAULT_WINDOW_DAYS = 30


def _resolve_window(start_date: date | None, end_date: date | None) -> tuple[date, date]:
    # Both bounds are optional: default to the trailing 30-day window so the
    # widgets render before the user touches the date picker. Shared by the
    # window-scoped endpoints (summary, matching funnel).
    end = end_date or datetime.now(UTC).date()
    start = start_date or end - timedelta(days=_DEFAULT_WINDOW_DAYS)
    if start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be on or before end_date",
        )
    return start, end


@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> DashboardSummary:
    start, end = _resolve_window(start_date, end_date)
    return await dashboard_service.get_summary(
        workspace_id=workspace_id, start_date=start, end_date=end
    )


@router.get("/matching-funnel", response_model=MatchingFunnel)
async def get_matching_funnel(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> MatchingFunnel:
    start, end = _resolve_window(start_date, end_date)
    return await dashboard_service.get_matching_funnel(
        workspace_id=workspace_id, start_date=start, end_date=end
    )


@router.get("/placements-over-time", response_model=PlacementsOverTime)
async def get_placements_over_time(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    end_date: Annotated[date | None, Query()] = None,
) -> PlacementsOverTime:
    # Chart spans the full Jan–Dec of the filter's end year; only that year matters
    # here, so `start_date` (if the client sends it) is ignored.
    year = (end_date or datetime.now(UTC).date()).year
    return await dashboard_service.get_placements_over_time(workspace_id=workspace_id, year=year)


@router.get("/activity", response_model=DashboardActivity)
async def get_activity(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    end_date: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> DashboardActivity:
    # Both activity lists in one call. `limit` bounds each list; `end_date` scopes
    # the top-companies month (recent applicants are always the latest overall).
    day = end_date or datetime.now(UTC).date()
    return await dashboard_service.get_activity(
        workspace_id=workspace_id, end_date=day, limit=limit
    )
