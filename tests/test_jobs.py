from httpx import AsyncClient

from app.core.config import settings


async def test_list_jobs_requires_auth(client: AsyncClient) -> None:
    # The slice is mounted with `auth_required`, so an unauthenticated request
    # is rejected before it ever reaches Mongo.
    response = await client.get(f"{settings.API_V1_PREFIX}/jobs")
    assert response.status_code == 401


async def test_import_jobs_requires_auth(client: AsyncClient) -> None:
    # The bulk-import endpoint sits behind the same `auth_required` guard, so an
    # unauthenticated request is rejected before it ever reaches Mongo.
    zero_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.post(
        f"{settings.API_V1_PREFIX}/jobs/import",
        json={"jobs": [{"title": "QA Engineer", "company_id": zero_uuid}]},
    )
    assert response.status_code == 401
