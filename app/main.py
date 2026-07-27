import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.mongodb import close_mongo, init_mongo


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_mongo()
    yield
    await close_mongo()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        debug=settings.APP_DEBUG,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # On Vercel's serverless runtime, ASGI lifespan startup is not guaranteed to
    # run, so ensure Mongo is initialized (idempotently) on the first request.
    # Local/uvicorn and tests keep relying on `lifespan` and are unaffected.
    if os.getenv("VERCEL"):

        @app.middleware("http")
        async def ensure_db(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            await init_mongo()
            return await call_next(request)

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)
    return app


app = create_app()
