import re
from uuid import UUID

from beanie.operators import In, Or, RegEx

from ..companies.companies_models import Company
from .jobs_models import Job, JobStatus
from .jobs_schemas import JobCreate, JobPatch


async def get_job(job_id: UUID) -> Job | None:
    return await Job.get(job_id)


async def get_companies_map(jobs: list[Job]) -> dict[UUID, Company]:
    # Batch-resolve the `company_id` foreign keys for a page of jobs in a single
    # query, keyed by id, so read endpoints can embed the company without N+1.
    ids = list({job.company_id for job in jobs})
    if not ids:
        return {}
    companies = await Company.find(In(Company.id, ids)).to_list()
    return {company.id: company for company in companies}


async def create_job(data: JobCreate) -> Job:
    job = Job(**data.model_dump())
    await job.insert()
    return job


async def list_jobs(
    limit: int,
    offset: int,
    q: str | None = None,
    company_id: UUID | None = None,
    status: JobStatus | None = None,
) -> tuple[list[Job], int]:
    query = Job.find_all()
    if company_id is not None:
        query = query.find(Job.company_id == company_id)
    if status is not None:
        query = query.find(Job.status == status)
    if q is not None:
        pattern = re.escape(q)
        query = query.find(
            Or(
                RegEx(Job.title, pattern, "i"),
                RegEx(Job.description, pattern, "i"),
                RegEx(Job.skills_required, pattern, "i"),
            )
        )
    total = await query.count()
    jobs = await query.sort("-created_at").skip(offset).limit(limit).to_list()
    return jobs, total


async def update_job(job: Job, data: JobPatch) -> Job:
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(job, field, value)
    await job.save()
    return job


async def delete_job(job: Job) -> bool:
    result = await job.delete()
    return result is not None and result.acknowledged
