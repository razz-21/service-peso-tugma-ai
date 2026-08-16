import re
from datetime import UTC, datetime
from uuid import UUID

from beanie.operators import Or, RegEx

from app.api.v1.routes.applicants.applicants_models import Applicant
from app.api.v1.routes.companies.companies_models import Company
from app.api.v1.routes.jobs.jobs_models import Job
from app.api.v1.routes.recommended_jobs.recommended_jobs_models import RecommendedJob
from app.core.blob import replace_avatar_blob

from .workspaces_models import Workspace
from .workspaces_schemas import WorkspaceCreate, WorkspacePatch, WorkspaceStatistics


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


async def set_workspace_avatar(workspace: Workspace, *, extension: str, data: bytes) -> Workspace:
    """Upload a new avatar image and store its Blob URL on the workspace."""
    workspace.avatar = await replace_avatar_blob(
        prefix="workspaces",
        entity_id=workspace.id,
        extension=extension,
        data=data,
        previous_url=workspace.avatar,
    )
    workspace.updated_at = datetime.now(UTC).isoformat()
    await workspace.save()
    return workspace


async def delete_workspace(workspace: Workspace) -> bool:
    result = await workspace.delete()
    return result.acknowledged


async def get_workspace_statistics(workspace_id: UUID) -> WorkspaceStatistics:
    total_applicants = await Applicant.find(Applicant.workspace_id == workspace_id).count()
    total_jobs = await Job.find(Job.workspace_id == workspace_id).count()
    total_companies = await Company.find(Company.workspace_id == workspace_id).count()
    total_recommended_jobs = await RecommendedJob.find(
        RecommendedJob.workspace_id == workspace_id
    ).count()
    return WorkspaceStatistics(
        total_applicants=total_applicants,
        total_jobs=total_jobs,
        total_companies=total_companies,
        total_recommended_jobs=total_recommended_jobs,
    )
