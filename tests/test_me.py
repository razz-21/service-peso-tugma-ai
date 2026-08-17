from httpx import AsyncClient

from app.core.config import settings


async def test_read_me_requires_auth(client: AsyncClient) -> None:
    # `/me` depends on `get_current_user`, so it rejects unauthenticated requests.
    response = await client.get(f"{settings.API_V1_PREFIX}/me")
    assert response.status_code == 401


async def test_upload_my_avatar_requires_auth(client: AsyncClient) -> None:
    # Avatar upload is gated on auth — rejected before any Blob call.
    response = await client.post(
        f"{settings.API_V1_PREFIX}/me/avatar",
        files={"file": ("avatar.png", b"\x89PNG\r\n", "image/png")},
    )
    assert response.status_code == 401


async def test_remove_my_avatar_requires_auth(client: AsyncClient) -> None:
    # Avatar removal is likewise gated on auth — rejected before any Blob call.
    response = await client.delete(f"{settings.API_V1_PREFIX}/me/avatar")
    assert response.status_code == 401
