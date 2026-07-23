from datetime import UTC, datetime
from uuid import UUID

from beanie.operators import In

from app.api.v1.routes.recommended_jobs.recommended_jobs_models import RecommendationScores

from ..companies.companies_models import Company
from ..jobs.jobs_models import Job
from ..users.users_models import User
from .applicant_jobs_models import ApplicantJob, ApplicantJobStatus

# A referral "holds a seat" against the job's vacancy count while the applicant
# is still in play; the seat is released once the application ends. The vacancy
# count is only adjusted when a status change crosses this boundary, so repeated
# saves or moves within the same group (e.g. withdrawn -> not_hired) never
# double-count.
_SEAT_OCCUPYING_STATUSES = frozenset(
    {
        ApplicantJobStatus.REFERRED,
        ApplicantJobStatus.INTERVIEW_SCHEDULED,
        ApplicantJobStatus.HIRED,
    }
)

# The lifecycle timestamp to stamp (once) when a referral first enters a status.
# NOT_HIRED has no dedicated timestamp field.
_STATUS_TIMESTAMP_FIELDS = {
    ApplicantJobStatus.INTERVIEW_SCHEDULED: "interview_on",
    ApplicantJobStatus.HIRED: "hired_on",
    ApplicantJobStatus.WITHDRAWN: "withdrawn_on",
}


def _occupies_seat(status: ApplicantJobStatus) -> bool:
    return status in _SEAT_OCCUPYING_STATUSES


async def create_applicant_job(
    *,
    applicant_id: UUID,
    job: Job,
    assigned_by: UUID,
    match_scores: RecommendationScores,
    workspace_id: UUID,
) -> ApplicantJob:
    # `company_id` is copied from the job so the referral always resolves to the
    # job's owning company; `referred_on` is stamped now since a fresh record
    # enters the lifecycle at REFERRED (the model's default status).
    applicant_job = ApplicantJob(
        applicant_id=applicant_id,
        job_id=job.id,
        company_id=job.company_id,
        assigned_by=assigned_by,
        match_scores=match_scores,
        referred_on=datetime.now(UTC).isoformat(),
        workspace_id=workspace_id,
    )
    await applicant_job.insert()
    # Referring the job consumes one open seat. The caller has already verified
    # an open vacancy exists; clamp at 0 defensively against races.
    job.no_of_vacancies = max(0, job.no_of_vacancies - 1)
    await job.save()
    return applicant_job


async def update_status(
    applicant_job: ApplicantJob,
    new_status: ApplicantJobStatus,
    job: Job | None,
) -> ApplicantJob:
    """Advance a referral to ``new_status``, adjusting the job's vacancy count.

    Moving a seat-holding referral to a released status (withdrawn / not hired)
    frees a vacancy (+1); moving a released referral back into play consumes one
    again (-1). ``job`` may be ``None`` if the underlying job was deleted, in
    which case only the referral is updated.
    """
    old_status = applicant_job.status
    if new_status != old_status and job is not None:
        # +1 when releasing a held seat, -1 when re-occupying, 0 otherwise.
        delta = int(_occupies_seat(old_status)) - int(_occupies_seat(new_status))
        if delta:
            job.no_of_vacancies = max(0, job.no_of_vacancies + delta)
            await job.save()

    applicant_job.status = new_status
    timestamp_field = _STATUS_TIMESTAMP_FIELDS.get(new_status)
    if timestamp_field is not None and getattr(applicant_job, timestamp_field) is None:
        setattr(applicant_job, timestamp_field, datetime.now(UTC).isoformat())
    await applicant_job.save()
    return applicant_job


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
