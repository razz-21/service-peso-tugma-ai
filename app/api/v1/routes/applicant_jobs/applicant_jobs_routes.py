from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user, get_current_workspace_id
from app.matching.primary_requirements import (
    civil_status_matches,
    has_open_vacancy,
    sex_matches,
)

from ..applicants import applicants_service
from ..companies import companies_service
from ..companies.companies_models import Company
from ..jobs import jobs_service
from ..jobs.jobs_models import Job, JobStatus
from ..users import users_service
from ..users.users_models import User
from . import applicant_jobs_service
from .applicant_jobs_models import ApplicantJob, ApplicantJobStatus
from .applicant_jobs_schemas import (
    ApplicantJobCompany,
    ApplicantJobCreate,
    ApplicantJobJob,
    ApplicantJobList,
    ApplicantJobRead,
    ApplicantJobUpdate,
    ApplicantJobUser,
)

router = APIRouter()


def _to_read(
    applicant_job: ApplicantJob,
    job: Job | None,
    company: Company | None,
    user: User | None,
) -> ApplicantJobRead:
    # Resolve the stored foreign keys into embedded summaries. Built explicitly
    # (rather than model_validate on the document) because the read model retypes
    # `assigned_by` from a UUID into a user summary.
    return ApplicantJobRead(
        id=applicant_job.id,
        job=ApplicantJobJob.model_validate(job) if job else None,
        company=ApplicantJobCompany.model_validate(company) if company else None,
        status=applicant_job.status,
        referred_on=applicant_job.referred_on,
        interview_on=applicant_job.interview_on,
        hired_on=applicant_job.hired_on,
        withdrawn_on=applicant_job.withdrawn_on,
        match_scores=applicant_job.match_scores,
        assigned_by=ApplicantJobUser.model_validate(user) if user else None,
        created_at=applicant_job.created_at,
    )


@router.post("", response_model=ApplicantJobRead, status_code=status.HTTP_201_CREATED)
async def create_applicant_job(
    data: ApplicantJobCreate,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ApplicantJobRead:
    # Refer the job to the applicant. Resolve both foreign keys within the
    # workspace first (reject cross-tenant/orphan references), then gate on the
    # job's hard primary requirements before creating the referral.
    job = await jobs_service.get_job(data.job_id, workspace_id=workspace_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Job not found")
    applicant = await applicants_service.get_applicant(data.applicant_id, workspace_id=workspace_id)
    if applicant is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Applicant not found")

    # `assigned_by` defaults to the authenticated user when the client omits it.
    assigned_by = data.assigned_by or current_user.id
    if data.assigned_by is not None and await users_service.get_user(data.assigned_by) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Assigned-by (user) not found"
        )

    # A referral is only allowed while the job is still active — a closed job
    # can't take new referrals.
    if job.status != JobStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot refer applicant: this job is no longer active",
        )

    # Validate the job's hard primary requirements before proceeding: an
    # applicant who categorically fails vacancies, sex, or civil status must not
    # be referred. Each check is "if applicable" — skipped when the job leaves
    # the field unspecified or the applicant lacks the datum.
    if not has_open_vacancy(job.no_of_vacancies):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Job has no open vacancies",
        )
    if not sex_matches(applicant.sex, job.sex):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Applicant's sex does not match the job's requirement",
        )
    if not civil_status_matches(applicant.civil_status, job.civil_status):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Applicant's civil status does not match the job's requirement",
        )

    applicant_job = await applicant_jobs_service.create_applicant_job(
        applicant_id=applicant.id,
        job=job,
        assigned_by=assigned_by,
        match_scores=data.match_scores,
        workspace_id=workspace_id,
    )
    company = await companies_service.get_company(job.company_id, workspace_id=workspace_id)
    user = await users_service.get_user(assigned_by)
    return _to_read(applicant_job, job, company, user)


@router.get("", response_model=ApplicantJobList)
async def list_applicant_jobs(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    job_id: Annotated[UUID | None, Query()] = None,
    company_id: Annotated[UUID | None, Query()] = None,
    status: Annotated[ApplicantJobStatus | None, Query()] = None,
    assigned_by: Annotated[UUID | None, Query()] = None,
) -> ApplicantJobList:
    applicant_jobs, total = await applicant_jobs_service.list_applicant_jobs(
        limit=limit,
        offset=offset,
        job_id=job_id,
        company_id=company_id,
        status=status,
        assigned_by=assigned_by,
        workspace_id=workspace_id,
    )
    jobs = await applicant_jobs_service.get_jobs_map(applicant_jobs, workspace_id=workspace_id)
    companies = await applicant_jobs_service.get_companies_map(
        applicant_jobs, workspace_id=workspace_id
    )
    users = await applicant_jobs_service.get_users_map(applicant_jobs)
    return ApplicantJobList(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            _to_read(
                applicant_job,
                jobs.get(applicant_job.job_id),
                companies.get(applicant_job.company_id),
                users.get(applicant_job.assigned_by),
            )
            for applicant_job in applicant_jobs
        ],
    )


@router.get("/{applicant_job_id}", response_model=ApplicantJobRead)
async def get_applicant_job(
    applicant_job_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> ApplicantJobRead:
    applicant_job = await applicant_jobs_service.get_applicant_job(
        applicant_job_id, workspace_id=workspace_id
    )
    if applicant_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Applicant job not found")
    job = await jobs_service.get_job(applicant_job.job_id, workspace_id=workspace_id)
    company = await companies_service.get_company(
        applicant_job.company_id, workspace_id=workspace_id
    )
    user = await users_service.get_user(applicant_job.assigned_by)
    return _to_read(applicant_job, job, company, user)


@router.patch("/{applicant_job_id}", response_model=ApplicantJobRead)
async def update_applicant_job(
    applicant_job_id: UUID,
    data: ApplicantJobUpdate,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> ApplicantJobRead:
    applicant_job = await applicant_jobs_service.get_applicant_job(
        applicant_job_id, workspace_id=workspace_id
    )
    if applicant_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Applicant job not found")
    # Fetch the job up front so the service can adjust its vacancy count when the
    # status change releases or re-consumes a seat (e.g. -> withdrawn / not hired).
    job = await jobs_service.get_job(applicant_job.job_id, workspace_id=workspace_id)
    applicant_job = await applicant_jobs_service.update_status(applicant_job, data.status, job)
    company = await companies_service.get_company(
        applicant_job.company_id, workspace_id=workspace_id
    )
    user = await users_service.get_user(applicant_job.assigned_by)
    return _to_read(applicant_job, job, company, user)
