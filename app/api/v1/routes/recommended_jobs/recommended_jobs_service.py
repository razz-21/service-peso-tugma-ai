from uuid import UUID

from beanie.operators import In

from ..jobs.jobs_models import Job
from .recommended_jobs_models import RecommendedJob, RecommendedJobStatus
from .recommended_jobs_schemas import RecommendedJobCreate, RecommendedJobPatch


async def get_recommended_job(
    recommended_job_id: UUID, workspace_id: UUID
) -> RecommendedJob | None:
    # Scoped to the workspace: a record belonging to another tenant resolves to
    # None, so callers surface it as a 404 rather than leaking cross-workspace data.
    return await RecommendedJob.find_one(
        RecommendedJob.id == recommended_job_id,
        RecommendedJob.workspace_id == workspace_id,
    )


async def get_jobs_map(
    recommendations: list[RecommendedJob], workspace_id: UUID
) -> dict[UUID, Job]:
    # Batch-resolve the `job_id` foreign keys for a page of recommendations in a
    # single query, keyed by id, so read endpoints can embed the job without N+1.
    ids = list({rec.job_id for rec in recommendations})
    if not ids:
        return {}
    jobs = await Job.find(In(Job.id, ids), Job.workspace_id == workspace_id).to_list()
    return {job.id: job for job in jobs}


async def create_recommended_job(
    data: RecommendedJobCreate, workspace_id: UUID, assessed_by: UUID
) -> RecommendedJob:
    recommended_job = RecommendedJob(
        **data.model_dump(exclude={"assessed_by"}),
        assessed_by=assessed_by,
        workspace_id=workspace_id,
    )
    await recommended_job.insert()
    return recommended_job


async def list_recommended_jobs(
    limit: int,
    offset: int,
    workspace_id: UUID,
    job_id: UUID | None = None,
    applicant_id: UUID | None = None,
    status: RecommendedJobStatus | None = None,
    is_relevant: bool | None = None,
    assessed_by: UUID | None = None,
) -> tuple[list[RecommendedJob], int]:
    query = RecommendedJob.find(RecommendedJob.workspace_id == workspace_id)
    if job_id is not None:
        query = query.find(RecommendedJob.job_id == job_id)
    if applicant_id is not None:
        query = query.find(RecommendedJob.applicant_id == applicant_id)
    if status is not None:
        query = query.find(RecommendedJob.status == status)
    if is_relevant is not None:
        query = query.find(RecommendedJob.is_relevant == is_relevant)
    if assessed_by is not None:
        query = query.find(RecommendedJob.assessed_by == assessed_by)
    total = await query.count()
    recommendations = await query.sort("-date_registered").skip(offset).limit(limit).to_list()
    return recommendations, total


async def update_recommended_job(
    recommended_job: RecommendedJob, data: RecommendedJobPatch
) -> RecommendedJob:
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(recommended_job, field, value)
    await recommended_job.save()
    return recommended_job


async def delete_recommended_job(recommended_job: RecommendedJob) -> bool:
    result = await recommended_job.delete()
    return result is not None and result.acknowledged
