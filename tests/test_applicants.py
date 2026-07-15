from httpx import AsyncClient

from app.core.config import settings


async def test_list_applicants_requires_auth(client: AsyncClient) -> None:
    # The slice is mounted with `auth_required`, so an unauthenticated request
    # is rejected before it ever reaches Mongo.
    response = await client.get(f"{settings.API_V1_PREFIX}/applicants")
    assert response.status_code == 401


async def test_create_applicant_requires_auth(client: AsyncClient) -> None:
    response = await client.post(
        f"{settings.API_V1_PREFIX}/applicants",
        json={"firstname": "Ada", "lastname": "Lovelace"},
    )
    assert response.status_code == 401
