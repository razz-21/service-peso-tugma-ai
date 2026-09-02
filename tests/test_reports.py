from httpx import AsyncClient

from app.api.v1.routes.reports.reports_service import _education_bucket
from app.core.config import settings


async def test_employment_summary_requires_auth(client: AsyncClient) -> None:
    # The slice is mounted with `auth_required`, so an unauthenticated request is
    # rejected before it ever reaches Mongo.
    response = await client.get(f"{settings.API_V1_PREFIX}/reports/employment-summary")
    assert response.status_code == 401


async def test_unemployed_by_education_requires_auth(client: AsyncClient) -> None:
    response = await client.get(f"{settings.API_V1_PREFIX}/reports/unemployed-by-education")
    assert response.status_code == 401


def test_education_bucket_maps_completed_and_in_progress_degrees() -> None:
    # A finished bachelor's degree reads as College Graduate; an in-progress one
    # (College Level / Undergraduate) reads as College Undergraduate, so the two
    # never collapse together.
    assert _education_bucket("College Graduate") == "College Graduate"
    assert _education_bucket("Bachelor of Science in IT") == "College Graduate"
    assert _education_bucket("College Undergraduate") == "College Undergraduate"
    assert _education_bucket("College Level") == "College Undergraduate"


def test_education_bucket_ranks_postgraduate_above_college() -> None:
    # Postgraduate is checked first, so a master's/doctorate reads as Postgraduate
    # rather than College Graduate.
    assert _education_bucket("Postgraduate") == "Postgraduate"
    assert _education_bucket("Master's Degree") == "Postgraduate"
    assert _education_bucket("Doctorate") == "Postgraduate"
    assert _education_bucket("PhD") == "Postgraduate"


def test_education_bucket_maps_basic_education_levels() -> None:
    assert _education_bucket("Senior High School") == "Senior High School"
    assert _education_bucket("Junior High School") == "Junior High School"
    # A generic (old-system) "High School" reads as Senior High School.
    assert _education_bucket("High School Graduate") == "Senior High School"
    assert _education_bucket("Elementary") == "Elementary"
    assert _education_bucket("Vocational") == "Vocational"


def test_education_bucket_handles_blank_and_unknown() -> None:
    # A blank attainment drops out of the breakdown (None); anything unrecognised
    # falls into the catch-all "Other".
    assert _education_bucket(None) is None
    assert _education_bucket("") is None
    assert _education_bucket("   ") is None
    assert _education_bucket("Something else") == "Other"
