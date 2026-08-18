import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.mongodb import close_mongo, init_mongo
from app.matching.embeddings import EmbeddingError


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

    # The job-matching pipeline calls a hosted embedding endpoint; when that
    # upstream is misconfigured or unreachable it raises `EmbeddingError`. Map it
    # to 502 (Bad Gateway) so the failing dependency is explicit — an uncaught
    # error would surface as an opaque 500 with no hint at the cause. The response
    # still passes back out through CORSMiddleware, so the browser can read it.
    @app.exception_handler(EmbeddingError)
    async def embedding_error_handler(_: Request, exc: EmbeddingError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": str(exc)},
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

    # Root liveness probe. Every route lives under `API_V1_PREFIX` (/api/v1), so
    # the bare domain would otherwise 404 — which reads as a failed deploy. This
    # gives a cheap "is it up?" check at `/` and points at the real API.
    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"status": "ok", "service": settings.PROJECT_NAME, "api": settings.API_V1_PREFIX}

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)
    return app


app = create_app()
