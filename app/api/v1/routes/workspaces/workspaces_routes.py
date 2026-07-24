from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import authorize_workspace_access, get_current_user, require_roles
from app.api.v1.routes.users.users_models import User, UserRole

from . import workspaces_service
from .workspaces_schemas import (
    WorkspaceCreate,
    WorkspaceList,
    WorkspacePatch,
    WorkspaceRead,
    WorkspaceStatistics,
)

router = APIRouter()


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    data: WorkspaceCreate,
    _: Annotated[User, Depends(require_roles(UserRole.SUPER_ADMIN))],
) -> WorkspaceRead:
    workspace = await workspaces_service.create_workspace(data)
    return WorkspaceRead.model_validate(workspace)


@router.get("", response_model=WorkspaceList)
async def list_workspaces(
    _: Annotated[User, Depends(require_roles(UserRole.SUPER_ADMIN))],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str, Query()] = None,
) -> WorkspaceList:
    workspaces, total = await workspaces_service.list_workspaces(limit=limit, offset=offset, q=q)
    return WorkspaceList(
        total=total,
        limit=limit,
        offset=offset,
        items=[WorkspaceRead.model_validate(workspace) for workspace in workspaces],
    )


@router.get("/{workspace_id}", response_model=WorkspaceRead)
async def get_workspace(
    workspace_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkspaceRead:
    authorize_workspace_access(current_user, workspace_id)
    workspace = await workspaces_service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return WorkspaceRead.model_validate(workspace)


@router.get("/{workspace_id}/statistics", response_model=WorkspaceStatistics)
async def get_workspace_statistics(
    workspace_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkspaceStatistics:
    authorize_workspace_access(current_user, workspace_id)
    workspace = await workspaces_service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return await workspaces_service.get_workspace_statistics(workspace_id)


@router.patch("/{workspace_id}", response_model=WorkspaceRead)
async def update_workspace(
    workspace_id: UUID,
    data: WorkspacePatch,
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkspaceRead:
    authorize_workspace_access(current_user, workspace_id)
    workspace = await workspaces_service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    workspace = await workspaces_service.update_workspace(workspace, data)
    return WorkspaceRead.model_validate(workspace)


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    authorize_workspace_access(current_user, workspace_id)
    workspace = await workspaces_service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if not await workspaces_service.delete_workspace(workspace):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete workspace",
        )
