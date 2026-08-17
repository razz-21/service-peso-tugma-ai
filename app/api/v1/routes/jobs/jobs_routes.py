from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user, get_current_workspace_id
from app.api.v1.routes.audit_logs import audit_logs_service
from app.api.v1.routes.audit_logs.audit_logs_models import AuditDiffRow, AuditEntity, AuditTone
from app.api.v1.routes.users.users_models import User

from ..companies import companies_service
from ..companies.companies_models import Company
from . import jobs_service
from .jobs_models import Job, JobStatus
from .jobs_schemas import (
    JobCompany,
    JobCreate,
    JobList,
    JobPatch,
    JobRead,
)

router = APIRouter()


async def _require_company(company_id: UUID, workspace_id: UUID) -> Company:
    # Enforce the `company_id` foreign key within the workspace: reject references
    # to companies that don't exist (or live in another tenant) rather than
    # allowing orphaned/cross-workspace jobs. Returns the company so the caller
    # can embed it in the response without a second lookup.
    company = await companies_service.get_company(company_id, workspace_id=workspace_id)
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company not found",
        )
    return company


def _to_read(job: Job, company: Company | None) -> JobRead:
    # Resolve the stored `company_id` into the embedded `company` summary.
    read = JobRead.model_validate(job)
    read.company = JobCompany.model_validate(company) if company else None
    return read


# Presentation labels for a job's active/inactive states in the audit feed.
_JOB_STATUS_LABEL = {JobStatus.ACTIVE: "Active", JobStatus.CLOSED: "Inactive"}


async def _record_job_update(
    actor: User,
    workspace_id: UUID,
    job: Job,
    company: Company | None,
    previous_status: JobStatus,
) -> None:
    # A status flip reads as an (de)activation with a before → after row; any
    # other edit is a plain update.
    records = [job.title] + ([company.company_name] if company else [])
    if job.status != previous_status:
        deactivated = job.status == JobStatus.CLOSED
        await audit_logs_service.record_audit(
            workspace_id=workspace_id,
            entity=AuditEntity.JOB,
            entity_label="Job",
            actor=actor.fullname,
            action="deactivated a job listing" if deactivated else "activated a job listing",
            icon="toggle_off" if deactivated else "toggle_on",
            icon_tone=AuditTone.AMBER if deactivated else AuditTone.GREEN,
            chip_tone=AuditTone.GREEN,
            records=records,
            diff=[
                AuditDiffRow(
                    label="Status",
                    from_=_JOB_STATUS_LABEL[previous_status],
                    to=_JOB_STATUS_LABEL[job.status],
                )
            ],
        )
        return
    await audit_logs_service.record_audit(
        workspace_id=workspace_id,
        entity=AuditEntity.JOB,
        entity_label="Job",
        actor=actor.fullname,
        action="updated a job listing",
        icon="work",
        icon_tone=AuditTone.GREEN,
        chip_tone=AuditTone.GREEN,
        records=records,
    )


@router.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job(
    data: JobCreate,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> JobRead:
    company = await _require_company(data.company_id, workspace_id)
    job = await jobs_service.create_job(data, workspace_id=workspace_id)
    await audit_logs_service.record_audit(
        workspace_id=workspace_id,
        entity=AuditEntity.JOB,
        entity_label="Job",
        actor=current_user.fullname,
        action="created a job listing",
        icon="work",
        icon_tone=AuditTone.GREEN,
        chip_tone=AuditTone.GREEN,
        records=[job.title, company.company_name],
    )
    return _to_read(job, company)


@router.get("", response_model=JobList)
async def list_jobs(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query()] = None,
    company_id: Annotated[UUID | None, Query()] = None,
    status: Annotated[JobStatus | None, Query()] = None,
) -> JobList:
    jobs, total = await jobs_service.list_jobs(
        limit=limit,
        offset=offset,
        q=q,
        company_id=company_id,
        status=status,
        workspace_id=workspace_id,
    )
    companies = await jobs_service.get_companies_map(jobs, workspace_id=workspace_id)
    return JobList(
        total=total,
        limit=limit,
        offset=offset,
        items=[_to_read(job, companies.get(job.company_id)) for job in jobs],
    )


@router.get("/{job_id}", response_model=JobRead)
async def get_job(
    job_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> JobRead:
    job = await jobs_service.get_job(job_id, workspace_id=workspace_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    company = await companies_service.get_company(job.company_id, workspace_id=workspace_id)
    return _to_read(job, company)


@router.patch("/{job_id}", response_model=JobRead)
async def update_job(
    job_id: UUID,
    data: JobPatch,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> JobRead:
    job = await jobs_service.get_job(job_id, workspace_id=workspace_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if data.company_id is not None:
        await _require_company(data.company_id, workspace_id)
    previous_status = job.status
    job = await jobs_service.update_job(job, data)
    company = await companies_service.get_company(job.company_id, workspace_id=workspace_id)
    await _record_job_update(current_user, workspace_id, job, company, previous_status)
    return _to_read(job, company)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    job = await jobs_service.get_job(job_id, workspace_id=workspace_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    referrals = await jobs_service.count_job_referrals(job_id, workspace_id=workspace_id)
    if referrals > 0:
        # Referential integrity: the job is used by applicant referral/placement
        # records. Refuse the delete rather than orphan them.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot delete this job because it is used by "
                f"{referrals} applicant referral record(s). "
            ),
        )
    job_title = job.title
    if not await jobs_service.delete_job(job):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete job",
        )
    await audit_logs_service.record_audit(
        workspace_id=workspace_id,
        entity=AuditEntity.JOB,
        entity_label="Job",
        actor=current_user.fullname,
        action="deleted a job listing",
        icon="work",
        icon_tone=AuditTone.RED,
        chip_tone=AuditTone.RED,
        records=[job_title],
    )
