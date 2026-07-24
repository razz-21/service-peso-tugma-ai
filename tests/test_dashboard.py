from datetime import date

from httpx import AsyncClient

from app.api.v1.routes.dashboard.dashboard_service import (
    _day_start_iso,
    _funnel_pct,
    _initials,
    _month_bounds,
    _months_series,
    _percent_change,
)
from app.core.config import settings


async def test_dashboard_summary_requires_auth(client: AsyncClient) -> None:
    # The slice is mounted with `auth_required`, so an unauthenticated request is
    # rejected before it ever reaches Mongo.
    response = await client.get(f"{settings.API_V1_PREFIX}/dashboard/summary")
    assert response.status_code == 401


def test_percent_change_period_over_period() -> None:
    # This window vs the preceding window of equal length, rounded to one decimal.
    assert _percent_change(108, 100) == 8.0  # up 8%
    assert _percent_change(9, 8) == 12.5
    assert _percent_change(80, 100) == -20.0  # can be negative


def test_percent_change_no_prior_data_is_none() -> None:
    # With an empty preceding window there's no baseline, so the percentage is
    # undefined regardless of this window's count.
    assert _percent_change(0, 0) is None
    assert _percent_change(5, 0) is None


def test_day_start_iso_is_utc_midnight() -> None:
    # Window boundaries are the start of the day in UTC, formatted like the stored
    # timestamps so string comparison stays chronological.
    assert _day_start_iso(date(2026, 6, 24)) == "2026-06-24T00:00:00+00:00"


async def test_placements_over_time_requires_auth(client: AsyncClient) -> None:
    response = await client.get(f"{settings.API_V1_PREFIX}/dashboard/placements-over-time")
    assert response.status_code == 401


def test_months_series_fills_all_twelve_months() -> None:
    # Sparse hire data still yields a full Jan–Dec axis, absent months as 0.
    months = _months_series({7: 187, 2: 120})
    assert [m.month for m in months] == list(range(1, 13))
    assert [m.label for m in months][:3] == ["Jan", "Feb", "Mar"]
    assert months[6].count == 187  # Jul
    assert months[1].count == 120  # Feb
    assert months[0].count == 0  # Jan — no data


async def test_matching_funnel_requires_auth(client: AsyncClient) -> None:
    response = await client.get(f"{settings.API_V1_PREFIX}/dashboard/matching-funnel")
    assert response.status_code == 401


def test_funnel_pct_is_share_of_base() -> None:
    # Each stage is a share of the Referred base, rounded to one decimal.
    assert _funnel_pct(512, 512) == 100.0
    assert _funnel_pct(318, 512) == 62.1
    assert _funnel_pct(187, 512) == 36.5


def test_funnel_pct_empty_base_is_zero() -> None:
    # No referrals in the window → flat 0% instead of dividing by zero.
    assert _funnel_pct(0, 0) == 0.0
    assert _funnel_pct(5, 0) == 0.0


async def test_activity_requires_auth(client: AsyncClient) -> None:
    response = await client.get(f"{settings.API_V1_PREFIX}/dashboard/activity")
    assert response.status_code == 401


def test_initials_from_first_two_words() -> None:
    assert _initials("Maria Santos") == "MS"
    assert _initials("Juan Miguel Dela Cruz") == "JM"  # first two words
    assert _initials("Company 1sadadasd") == "C1"
    assert _initials("BrightPath") == "BR"  # single word → first two letters
    assert _initials("") == ""


def test_month_bounds_wraps_december() -> None:
    # Half-open [month start, next month start); December rolls into next January.
    assert _month_bounds(date(2026, 7, 15)) == (
        "2026-07-01T00:00:00+00:00",
        "2026-08-01T00:00:00+00:00",
    )
    assert _month_bounds(date(2026, 12, 31)) == (
        "2026-12-01T00:00:00+00:00",
        "2027-01-01T00:00:00+00:00",
    )
