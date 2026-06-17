from httpx import AsyncClient

from app.core.config import settings


async def test_healthz(client: AsyncClient) -> None:
    response = await client.get(f"{settings.API_V1_PREFIX}/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
