from httpx import AsyncClient

from app.core.config import settings


async def test_list_companies_requires_auth(client: AsyncClient) -> None:
    # The slice is mounted with `auth_required`, so an unauthenticated request
    # is rejected before it ever reaches Mongo.
    response = await client.get(f"{settings.API_V1_PREFIX}/companies")
    assert response.status_code == 401


async def test_upload_avatar_requires_auth(client: AsyncClient) -> None:
    # Avatar upload is likewise gated on auth — rejected before any Blob/Mongo call.
    response = await client.post(
        f"{settings.API_V1_PREFIX}/companies/00000000-0000-0000-0000-000000000000/avatar",
        files={"file": ("logo.png", b"\x89PNG\r\n", "image/png")},
    )
    assert response.status_code == 401


async def test_remove_avatar_requires_auth(client: AsyncClient) -> None:
    # Avatar removal is likewise gated on auth — rejected before any Blob/Mongo call.
    response = await client.delete(
        f"{settings.API_V1_PREFIX}/companies/00000000-0000-0000-0000-000000000000/avatar",
    )
    assert response.status_code == 401
