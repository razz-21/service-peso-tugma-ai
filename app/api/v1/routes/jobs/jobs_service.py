import re
from uuid import UUID

from beanie.operators import In, RegEx

from ..companies.companies_models import Company
from .jobs_models import Job, JobStatus
from .jobs_schemas import JobCreate, JobPatch


async def get_job(job_id: UUID, workspace_id: UUID) -> Job | None:
    # Scoped to the workspace: a job belonging to another tenant resolves to None,
    # so callers surface it as a 404 rather than leaking cross-workspace data.
    return await Job.find_one(Job.id == job_id, Job.workspace_id == workspace_id)


async def get_companies_map(jobs: list[Job], workspace_id: UUID) -> dict[UUID, Company]:
    # Batch-resolve the `company_id` foreign keys for a page of jobs in a single
    # query, keyed by id, so read endpoints can embed the company without N+1.
    # Scoped to the workspace to match the jobs being resolved.
    ids = list({job.company_id for job in jobs})
    if not ids:
        return {}
    companies = await Company.find(
        In(Company.id, ids), Company.workspace_id == workspace_id
    ).to_list()
    return {company.id: company for company in companies}


async def create_job(data: JobCreate, workspace_id: UUID) -> Job:
    # `created_at` is excluded from the spread so it never overrides the model's
    # now() default with None; it's applied explicitly when the officer set it
    # (backdating a posting to a previous creation date).
    job = Job(**data.model_dump(exclude={"created_at"}), workspace_id=workspace_id)
    if data.created_at is not None:
        job.created_at = data.created_at
    await job.insert()
    return job


async def list_jobs(
    limit: int,
    offset: int,
    workspace_id: UUID,
    q: str | None = None,
    company_id: UUID | None = None,
    status: JobStatus | None = None,
) -> tuple[list[Job], int]:
    query = Job.find(Job.workspace_id == workspace_id)
    if company_id is not None:
        query = query.find(Job.company_id == company_id)
    if status is not None:
        query = query.find(Job.status == status)
    if q is not None:
        pattern = re.escape(q)
        query = query.find(RegEx(Job.title, pattern, "i"))
    total = await query.count()
    jobs = await query.sort("-created_at").skip(offset).limit(limit).to_list()
    return jobs, total


async def update_job(job: Job, data: JobPatch) -> Job:
    changes = data.model_dump(exclude_unset=True)
    # `created_at` is editable (backdating), but never clearable — drop a stray
    # null so it can only be set to a real date, never wiped.
    if changes.get("created_at") is None:
        changes.pop("created_at", None)
    for field, value in changes.items():
        setattr(job, field, value)
    await job.save()
    return job


async def count_job_referrals(job_id: UUID, workspace_id: UUID) -> int:
    # How many referral records point at this job. Referrals live in
    # `recommended_jobs`: a row with a non-null `status` means an officer has
    # referred an applicant to this job and advanced it through the referral
    # lifecycle (see recommended_jobs preserve logic). Such a job is "in use" and
    # must not be deleted, or that referral history would be orphaned onto a
    # non-existent job. Rows with `status is None` are fresh, auto-generated
    # recommendations (re-pruned on regeneration) and are excluded on purpose.
    # Scoped to the workspace to match the job's tenant.
    #
    # Imported lazily: a module-level import pulls in the recommended_jobs package
    # during startup and closes an import cycle back through app.matching.
    from beanie.operators import NE

    from app.api.v1.routes.recommended_jobs.recommended_jobs_models import RecommendedJob

    return await RecommendedJob.find(
        RecommendedJob.job_id == job_id,
        RecommendedJob.workspace_id == workspace_id,
        NE(RecommendedJob.status, None),
    ).count()


async def delete_job(job: Job) -> bool:
    result = await job.delete()
    return result is not None and result.acknowledged
