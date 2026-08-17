from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.api.deps import authorize_workspace_access, get_current_user, require_roles
from app.api.v1.routes.audit_logs import audit_logs_service
from app.api.v1.routes.audit_logs.audit_logs_models import AuditDiffRow, AuditEntity, AuditTone
from app.api.v1.routes.users.users_models import User, UserRole
from app.core.blob import AVATAR_CONTENT_TYPE_EXTENSIONS, AVATAR_MAX_BYTES

from . import workspaces_service
from .workspaces_models import MatchingScore, Workspace
from .workspaces_schemas import (
    WorkspaceCreate,
    WorkspaceList,
    WorkspacePatch,
    WorkspaceRead,
    WorkspaceStatistics,
)

router = APIRouter()

# Human labels for the matching-score weights shown in a settings audit diff.
_WEIGHT_LABELS = {
    "semantic_similarity": "Semantic Similarity",
    "skills_match": "Skills Match",
    "experience_match": "Experience Match",
    "educational_match": "Education Match",
    "location_preference": "Location Preference",
}


def _weight_diff(before: MatchingScore, after: MatchingScore) -> list[AuditDiffRow]:
    # One before → after row per weight that actually changed, formatted as `45%`.
    rows: list[AuditDiffRow] = []
    for field, label in _WEIGHT_LABELS.items():
        old = getattr(before, field)
        new = getattr(after, field)
        if old != new:
            rows.append(AuditDiffRow(label=label, from_=f"{old:g}%", to=f"{new:g}%"))
    return rows


async def _record_workspace_update(
    actor: User,
    workspace: Workspace,
    data: WorkspacePatch,
    previous_weights: MatchingScore,
) -> None:
    # A weights change is a Settings event carrying a per-weight diff; any other
    # edit is a plain Workspace update. Both can fire from a single patch.
    changed = set(data.model_dump(exclude_unset=True)) - {"updated_at"}
    if "matching_score" in changed:
        rows = _weight_diff(previous_weights, workspace.matching_score)
        if rows:
            await audit_logs_service.record_audit(
                workspace_id=workspace.id,
                entity=AuditEntity.SETTINGS,
                entity_label="Settings",
                actor=actor.fullname,
                action="updated the match scoring weights",
                icon="tune",
                icon_tone=AuditTone.GREEN,
                chip_tone=AuditTone.GREEN,
                records=[workspace.name],
                diff=rows,
            )
    if changed - {"matching_score"}:
        await audit_logs_service.record_audit(
            workspace_id=workspace.id,
            entity=AuditEntity.WORKSPACES,
            entity_label="Workspace",
            actor=actor.fullname,
            action="updated a workspace",
            icon="workspaces",
            icon_tone=AuditTone.GREEN,
            chip_tone=AuditTone.GREEN,
            records=[workspace.name],
        )


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    data: WorkspaceCreate,
    current_user: Annotated[User, Depends(require_roles(UserRole.SUPER_ADMIN))],
) -> WorkspaceRead:
    workspace = await workspaces_service.create_workspace(data)
    await audit_logs_service.record_audit(
        workspace_id=workspace.id,
        entity=AuditEntity.WORKSPACES,
        entity_label="Workspace",
        actor=current_user.fullname,
        action="created a workspace",
        icon="workspaces",
        icon_tone=AuditTone.GREEN,
        chip_tone=AuditTone.GREEN,
        records=[workspace.name],
    )
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
    previous_weights = workspace.matching_score.model_copy()
    workspace = await workspaces_service.update_workspace(workspace, data)
    await _record_workspace_update(current_user, workspace, data, previous_weights)
    return WorkspaceRead.model_validate(workspace)


@router.post("/{workspace_id}/avatar", response_model=WorkspaceRead)
async def upload_workspace_avatar(
    workspace_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File()],
) -> WorkspaceRead:
    # Uploads the image to Vercel Blob and stores its URL on the workspace. Rejects
    # unsupported types (415), empty (400), and files over 5 MB (413).
    authorize_workspace_access(current_user, workspace_id)
    workspace = await workspaces_service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    extension = AVATAR_CONTENT_TYPE_EXTENSIONS.get(file.content_type or "")
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPEG, PNG, WebP, or GIF images are supported.",
        )
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded image is empty.",
        )
    if len(data) > AVATAR_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Image exceeds the 5 MB limit.",
        )
    workspace = await workspaces_service.set_workspace_avatar(
        workspace, extension=extension, data=data
    )
    await audit_logs_service.record_audit(
        workspace_id=workspace.id,
        entity=AuditEntity.WORKSPACES,
        entity_label="Workspace",
        actor=current_user.fullname,
        action="updated a workspace avatar",
        icon="workspaces",
        icon_tone=AuditTone.GREEN,
        chip_tone=AuditTone.GREEN,
        records=[workspace.name],
    )
    return WorkspaceRead.model_validate(workspace)


@router.delete("/{workspace_id}/avatar", response_model=WorkspaceRead)
async def remove_workspace_avatar(
    workspace_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkspaceRead:
    # Clears the avatar URL and removes the stored Blob; a no-op when unset.
    authorize_workspace_access(current_user, workspace_id)
    workspace = await workspaces_service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    workspace = await workspaces_service.clear_workspace_avatar(workspace)
    await audit_logs_service.record_audit(
        workspace_id=workspace.id,
        entity=AuditEntity.WORKSPACES,
        entity_label="Workspace",
        actor=current_user.fullname,
        action="removed a workspace avatar",
        icon="workspaces",
        icon_tone=AuditTone.GREEN,
        chip_tone=AuditTone.GREEN,
        records=[workspace.name],
    )
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
    workspace_name = workspace.name
    if not await workspaces_service.delete_workspace(workspace):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete workspace",
        )
    await audit_logs_service.record_audit(
        workspace_id=workspace_id,
        entity=AuditEntity.WORKSPACES,
        entity_label="Workspace",
        actor=current_user.fullname,
        action="deleted a workspace",
        icon="workspaces",
        icon_tone=AuditTone.RED,
        chip_tone=AuditTone.RED,
        records=[workspace_name],
    )
