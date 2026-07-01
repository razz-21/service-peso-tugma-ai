import re
from uuid import UUID

from beanie.operators import Or, RegEx

from .workspaces_models import Workspace
from .workspaces_schemas import WorkspaceCreate, WorkspacePatch


async def get_workspace(workspace_id: UUID) -> Workspace | None:
    return await Workspace.get(workspace_id)


async def create_workspace(data: WorkspaceCreate) -> Workspace:
    workspace = Workspace(**data.model_dump())
    await workspace.insert()
    return workspace


async def list_workspaces(
    limit: int, offset: int, q: str | None = None
) -> tuple[list[Workspace], int]:
    query = Workspace.find_all()
    if q is not None:
        pattern = re.escape(q)
        query = query.find(
            Or(RegEx(Workspace.name, pattern, "i"), RegEx(Workspace.description, pattern, "i"))
        )
    total = await query.count()
    workspaces = await query.sort("-created_at").skip(offset).limit(limit).to_list()
    return workspaces, total


async def update_workspace(workspace: Workspace, data: WorkspacePatch) -> Workspace:
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(workspace, field, value)
    await workspace.save()
    return workspace


async def delete_workspace(workspace: Workspace) -> bool:
    result = await workspace.delete()
    return result.acknowledged
