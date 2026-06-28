from typing import Any

from beanie import init_beanie
from pymongo import AsyncMongoClient

from app.api.v1.routes.users.users_models import User
from app.core.config import settings

_client: AsyncMongoClient[dict[str, Any]] | None = None


async def init_mongo() -> None:
    global _client
    # Beanie 2.x uses PyMongo's native async driver (`AsyncMongoClient`).
    # `uuidRepresentation="standard"` is required so UUID `_id` values
    # (see app/api/v1/routes/users/users_models.py) encode/decode correctly.
    _client = AsyncMongoClient(settings.MONGODB_URI, uuidRepresentation="standard")
    await init_beanie(
        database=_client[settings.MONGODB_DB_NAME],
        document_models=[User],
    )


async def close_mongo() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
