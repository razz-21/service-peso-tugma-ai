from uuid import UUID

from beanie.operators import In

from ..companies.companies_models import Company
from ..jobs.jobs_models import Job
from ..users.users_models import User
from .applicant_jobs_models import ApplicantJob, ApplicantJobStatus


async def get_jobs_map(applicant_jobs: list[ApplicantJob], workspace_id: UUID) -> dict[UUID, Job]:
    # Batch-resolve the `job_id` foreign keys for a page of records in a single
    # query, keyed by id, so read endpoints can embed the job without N+1.
    ids = list({applicant_job.job_id for applicant_job in applicant_jobs})
    if not ids:
        return {}
    jobs = await Job.find(In(Job.id, ids), Job.workspace_id == workspace_id).to_list()
    return {job.id: job for job in jobs}


async def get_companies_map(
    applicant_jobs: list[ApplicantJob], workspace_id: UUID
) -> dict[UUID, Company]:
    ids = list({applicant_job.company_id for applicant_job in applicant_jobs})
    if not ids:
        return {}
    companies = await Company.find(
        In(Company.id, ids), Company.workspace_id == workspace_id
    ).to_list()
    return {company.id: company for company in companies}


async def get_users_map(applicant_jobs: list[ApplicantJob]) -> dict[UUID, User]:
    # Users are not workspace-scoped (see users_service.get_user).
    ids = list({applicant_job.assigned_by for applicant_job in applicant_jobs})
    if not ids:
        return {}
    users = await User.find(In(User.id, ids)).to_list()
    return {user.id: user for user in users}


async def get_applicant_job(applicant_job_id: UUID, workspace_id: UUID) -> ApplicantJob | None:
    # Scoped to the workspace: a record belonging to another tenant resolves to
    # None, so callers surface it as a 404 rather than leaking cross-workspace data.
    return await ApplicantJob.find_one(
        ApplicantJob.id == applicant_job_id,
        ApplicantJob.workspace_id == workspace_id,
    )


async def list_applicant_jobs(
    limit: int,
    offset: int,
    workspace_id: UUID,
    job_id: UUID | None = None,
    company_id: UUID | None = None,
    status: ApplicantJobStatus | None = None,
    assigned_by: UUID | None = None,
) -> tuple[list[ApplicantJob], int]:
    query = ApplicantJob.find(ApplicantJob.workspace_id == workspace_id)
    if job_id is not None:
        query = query.find(ApplicantJob.job_id == job_id)
    if company_id is not None:
        query = query.find(ApplicantJob.company_id == company_id)
    if status is not None:
        query = query.find(ApplicantJob.status == status)
    if assigned_by is not None:
        query = query.find(ApplicantJob.assigned_by == assigned_by)
    total = await query.count()
    applicant_jobs = await query.sort("-created_at").skip(offset).limit(limit).to_list()
    return applicant_jobs, total
