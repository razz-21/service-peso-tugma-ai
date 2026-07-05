from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.api.v1.routes import auth, companies, health, jobs, me, users, workspaces

# Routes that require an authenticated session. Applying the dependency at the
# include level guards every endpoint under the prefix and returns 401 when no
# valid session cookie/token is present. FastAPI caches the resolved dependency
# per request, so handlers that also take `get_current_user` (e.g. `/me`) don't
# incur a second lookup.
auth_required = [Depends(get_current_user)]

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(me.router, prefix="/me", tags=["me"], dependencies=auth_required)
api_router.include_router(users.router, prefix="/users", tags=["users"], dependencies=auth_required)
api_router.include_router(
    workspaces.router, prefix="/workspace", tags=["workspaces"], dependencies=auth_required
)
api_router.include_router(
    companies.router, prefix="/companies", tags=["companies"], dependencies=auth_required
)
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"], dependencies=auth_required)
