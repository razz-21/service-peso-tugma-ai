from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.mongodb import close_mongo, init_mongo


@asynccontextmanager
async def lifespan(_: FastAPI):
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

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)
    return app


app = create_app()
