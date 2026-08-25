from httpx import AsyncClient

from app.api.v1.routes.reports.reports_service import _is_employed
from app.core.config import settings


async def test_employment_summary_requires_auth(client: AsyncClient) -> None:
    # The slice is mounted with `auth_required`, so an unauthenticated request is
    # rejected before it ever reaches Mongo.
    response = await client.get(f"{settings.API_V1_PREFIX}/reports/employment-summary")
    assert response.status_code == 401


def test_is_employed_recognises_working_statuses() -> None:
    # The three "working" categories from the applicant form count as employed.
    assert _is_employed("Employed") is True
    assert _is_employed("Self-employed") is True
    assert _is_employed("Underemployed") is True
    assert _is_employed("  employed  ") is True  # normalised (trimmed/lower-cased)


def test_is_employed_treats_unemployed_and_blank_as_not_employed() -> None:
    # "Unemployed" contains the substring "employed" but must read as unemployed;
    # a missing/blank status is unemployed too (the default for a new job seeker).
    assert _is_employed("Unemployed") is False
    assert _is_employed(None) is False
    assert _is_employed("") is False
    assert _is_employed("   ") is False
