from fastapi import APIRouter

from app.api.v1.routes import auth, health, me, users, workspaces

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(me.router, prefix="/me", tags=["me"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(workspaces.router, prefix="/workspace", tags=["workspaces"])
