import asyncio
from typing import Any

from beanie import init_beanie
from pymongo import AsyncMongoClient

from app.api.v1.routes.applicants.applicants_models import Applicant
from app.api.v1.routes.audit_logs.audit_logs_models import AuditLog
from app.api.v1.routes.companies.companies_models import Company
from app.api.v1.routes.files.files_models import FileObject
from app.api.v1.routes.jobs.jobs_models import Job
from app.api.v1.routes.recommended_jobs.recommended_jobs_models import RecommendedJob
from app.api.v1.routes.users.users_models import User
from app.api.v1.routes.workspaces.workspaces_models import Workspace
from app.core.config import settings
from app.core.rate_limit_models import RateLimitCounter

_client: AsyncMongoClient[dict[str, Any]] | None = None
_init_lock = asyncio.Lock()


async def init_mongo() -> None:
    # Idempotent: safe to call from both the app lifespan and (on serverless,
    # where ASGI lifespan startup is not guaranteed to run) a per-request guard.
    # Concurrent cold-start requests are serialized by the lock so Beanie is
    # initialized exactly once.
    global _client
    if _client is not None:
        return
    async with _init_lock:
        if _client is not None:
            return
        # Beanie 2.x uses PyMongo's native async driver (`AsyncMongoClient`).
        # `uuidRepresentation="standard"` is required so UUID `_id` values
        # (see app/api/v1/routes/users/users_models.py) encode/decode correctly.
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            settings.MONGODB_URI, uuidRepresentation="standard"
        )
        await init_beanie(
            database=client[settings.MONGODB_DB_NAME],
            document_models=[
                User,
                Workspace,
                Company,
                Job,
                Applicant,
                RecommendedJob,
                AuditLog,
                FileObject,
                RateLimitCounter,
            ],
        )
        _client = client


async def close_mongo() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
