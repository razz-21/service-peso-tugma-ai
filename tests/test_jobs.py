from httpx import AsyncClient

from app.core.config import settings


async def test_list_jobs_requires_auth(client: AsyncClient) -> None:
    # The slice is mounted with `auth_required`, so an unauthenticated request
    # is rejected before it ever reaches Mongo.
    response = await client.get(f"{settings.API_V1_PREFIX}/jobs")
    assert response.status_code == 401
