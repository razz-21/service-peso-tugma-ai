from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.models.user import User

_client: AsyncIOMotorClient | None = None


async def init_mongo() -> None:
    global _client
    _client = AsyncIOMotorClient(settings.MONGODB_URI)
    await init_beanie(
        database=_client[settings.MONGODB_DB_NAME],
        document_models=[User],
    )


async def close_mongo() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
