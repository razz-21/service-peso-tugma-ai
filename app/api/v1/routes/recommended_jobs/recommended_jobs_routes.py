from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import get_current_user, get_current_workspace_id
from app.api.v1.routes.audit_logs import audit_logs_service
from app.api.v1.routes.audit_logs.audit_logs_models import AuditEntity, AuditTone
from app.api.v1.routes.users.users_models import User
from app.matching.primary_requirements import has_open_vacancy

from ..applicants import applicants_service
from ..companies import companies_service
from ..companies.companies_models import Company
from ..jobs import jobs_service
from ..jobs.jobs_models import Job, JobStatus
from ..users import users_service
from ..workspaces import workspaces_service
from . import recommended_jobs_service
from .recommended_jobs_models import RecommendedJob, RecommendedJobStatus
from .recommended_jobs_schemas import (
    RecommendedJobCompany,
    RecommendedJobCreate,
    RecommendedJobGenerate,
    RecommendedJobJob,
    RecommendedJobList,
    RecommendedJobPatch,
    RecommendedJobRead,
    RecommendedJobUser,
)

router = APIRouter()


async def _require_job(job_id: UUID, workspace_id: UUID) -> Job:
    # Enforce the `job_id` foreign key within the workspace: reject references to
    # jobs that don't exist (or live in another tenant) rather than allowing
    # orphaned/cross-workspace recommendations. Returns the job so the caller can
    # embed it in the response without a second lookup.
    job = await jobs_service.get_job(job_id, workspace_id=workspace_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job not found",
        )
    return job


async def _require_applicant(applicant_id: UUID, workspace_id: UUID) -> None:
    # Enforce the `applicant_id` foreign key within the workspace: reject
    # references to applicants that don't exist (or live in another tenant),
    # so a recommendation can't be orphaned from its applicant.
    if await applicants_service.get_applicant(applicant_id, workspace_id=workspace_id) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Applicant not found",
        )


async def _require_assessor(user_id: UUID) -> None:
    # Enforce the `assessed_by` foreign key: reject references to users that
    # don't exist.
    if await users_service.get_user(user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Assessor (user) not found",
        )


def _to_read(
    recommended_job: RecommendedJob,
    job: Job | None,
    company: Company | None = None,
    user: User | None = None,
) -> RecommendedJobRead:
    # Resolve the stored `job_id` into the embedded `job` summary, the job's
    # `company_id` into the nested `company` summary, and `assessed_by` into the
    # `assessor` summary ({ id, name, avatar }).
    read = RecommendedJobRead.model_validate(recommended_job)
    read.assessor = RecommendedJobUser.model_validate(user) if user else None
    if job is None:
        read.job = None
        return read
    job_summary = RecommendedJobJob.model_validate(job)
    job_summary.company = RecommendedJobCompany.model_validate(company) if company else None
    read.job = job_summary
    return read


def _to_read_mapped(
    recommended_job: RecommendedJob,
    jobs: dict[UUID, Job],
    companies: dict[UUID, Company],
    users: dict[UUID, User],
) -> RecommendedJobRead:
    # Build a read from pre-fetched job/company/user maps (batched list responses).
    job = jobs.get(recommended_job.job_id)
    company = companies.get(job.company_id) if job else None
    user = users.get(recommended_job.assessed_by)
    return _to_read(recommended_job, job, company, user)


@router.post("", response_model=RecommendedJobRead, status_code=status.HTTP_201_CREATED)
async def create_recommended_job(
    data: RecommendedJobCreate,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RecommendedJobRead | Response:
    job = await _require_job(data.job_id, workspace_id)
    if job.status != JobStatus.ACTIVE:
        # A referral (created directly in a vacancy-holding status, e.g. a manual
        # 'referred') to a closed job is rejected with a message, since referring
        # to an inactive job is a real error the officer must see. A plain
        # recommendation on a closed job is instead silently skipped (nothing
        # created, no error) so it simply never surfaces.
        if recommended_jobs_service.starts_holding_vacancy(None, data.status):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot refer applicant: this job is no longer active",
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    await _require_applicant(data.applicant_id, workspace_id)
    # `assessed_by` defaults to the authenticated user when the client omits it.
    assessed_by = data.assessed_by or current_user.id
    if data.assessed_by is not None:
        await _require_assessor(data.assessed_by)
    recommended_job = await recommended_jobs_service.create_recommended_job(
        data, workspace_id=workspace_id, assessed_by=assessed_by
    )
    company = await companies_service.get_company(job.company_id, workspace_id=workspace_id)
    user = await users_service.get_user(recommended_job.assessed_by)
    if recommended_jobs_service.starts_holding_vacancy(None, recommended_job.status):
        await audit_logs_service.record_audit(
            workspace_id=workspace_id,
            entity=AuditEntity.REFERRALS,
            entity_label="Referral",
            actor=current_user.fullname,
            action="referred an applicant to",
            icon="send",
            icon_tone=AuditTone.GREEN,
            chip_tone=AuditTone.GREEN,
            records=[job.title] + ([company.company_name] if company else []),
        )
    return _to_read(recommended_job, job, company, user)


@router.post(
    "/generate",
    response_model=list[RecommendedJobRead],
    status_code=status.HTTP_201_CREATED,
)
async def generate_recommendations(
    data: RecommendedJobGenerate,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[RecommendedJobRead]:
    # Run the AI pipeline for the applicant and persist the Top-K, then resolve
    # each recommendation's job into the embedded summary for the response.
    applicant = await applicants_service.get_applicant(data.applicant_id, workspace_id=workspace_id)
    if applicant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Applicant not found")
    workspace = await workspaces_service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    recommendations = await recommended_jobs_service.generate_recommendations(
        applicant, workspace, assessed_by=current_user.id, top_k=data.top_k
    )
    jobs = await recommended_jobs_service.get_jobs_map(recommendations, workspace_id=workspace_id)
    companies = await jobs_service.get_companies_map(list(jobs.values()), workspace_id=workspace_id)
    users = await recommended_jobs_service.get_users_map(recommendations)
    return [_to_read_mapped(rec, jobs, companies, users) for rec in recommendations]


@router.get("", response_model=RecommendedJobList)
async def list_recommended_jobs(
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    job_id: Annotated[UUID | None, Query()] = None,
    applicant_id: Annotated[UUID | None, Query()] = None,
    status: Annotated[RecommendedJobStatus | None, Query()] = None,
    is_relevant: Annotated[bool | None, Query()] = None,
    assessed_by: Annotated[UUID | None, Query()] = None,
) -> RecommendedJobList:
    recommendations, total = await recommended_jobs_service.list_recommended_jobs(
        limit=limit,
        offset=offset,
        job_id=job_id,
        applicant_id=applicant_id,
        status=status,
        is_relevant=is_relevant,
        assessed_by=assessed_by,
        workspace_id=workspace_id,
    )
    jobs = await recommended_jobs_service.get_jobs_map(recommendations, workspace_id=workspace_id)
    companies = await jobs_service.get_companies_map(list(jobs.values()), workspace_id=workspace_id)
    users = await recommended_jobs_service.get_users_map(recommendations)
    return RecommendedJobList(
        total=total,
        limit=limit,
        offset=offset,
        items=[_to_read_mapped(rec, jobs, companies, users) for rec in recommendations],
    )


@router.get("/{recommended_job_id}", response_model=RecommendedJobRead)
async def get_recommended_job(
    recommended_job_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> RecommendedJobRead:
    recommended_job = await recommended_jobs_service.get_recommended_job(
        recommended_job_id, workspace_id=workspace_id
    )
    if recommended_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recommended job not found"
        )
    job = await jobs_service.get_job(recommended_job.job_id, workspace_id=workspace_id)
    company = (
        await companies_service.get_company(job.company_id, workspace_id=workspace_id)
        if job is not None
        else None
    )
    user = await users_service.get_user(recommended_job.assessed_by)
    return _to_read(recommended_job, job, company, user)


@router.patch("/{recommended_job_id}", response_model=RecommendedJobRead)
async def update_recommended_job(
    recommended_job_id: UUID,
    data: RecommendedJobPatch,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
    current_user: Annotated[User, Depends(get_current_user)],
    ) -> RecommendedJobRead:
    recommended_job = await recommended_jobs_service.get_recommended_job(
        recommended_job_id, workspace_id=workspace_id
    )

    previous_status = recommended_job.status
    if recommended_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recommended job not found"
        )
    if data.job_id is not None:
        await _require_job(data.job_id, workspace_id)
    if data.applicant_id is not None:
        await _require_applicant(data.applicant_id, workspace_id)
    if data.assessed_by is not None:
        await _require_assessor(data.assessed_by)
    # Enforce the referral lifecycle: terminal statuses are final and `resigned`
    # is reachable only from `hired`. Rejected before any vacancy accounting so
    # an illegal transition never touches the job's seat count.
    if data.status is not None:
        transition_error = recommended_jobs_service.status_transition_error(
            recommended_job.status, data.status
        )
        if transition_error is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=transition_error,
            )
    # Referring the applicant (moving the recommendation into a vacancy-holding
    # status) consumes one of the job's open seats — gate it on an open vacancy,
    # mirroring the applicant_jobs referral path. Only a transition that *starts*
    # holding a seat is gated; advancing between holding states or releasing one
    # is unaffected.
    if data.status is not None and recommended_jobs_service.starts_holding_vacancy(
        recommended_job.status, data.status
    ):
        job = await jobs_service.get_job(recommended_job.job_id, workspace_id=workspace_id)
        if job is not None:
            # Referring an applicant is only allowed while the job is still
            # active — a closed job can't take new referrals.
            if job.status != JobStatus.ACTIVE:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Cannot refer applicant: this job is no longer active",
                )
            if not has_open_vacancy(job.no_of_vacancies):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Job has no open vacancies",
                )
    recommended_job = await recommended_jobs_service.update_recommended_job(recommended_job, data)
    job = await jobs_service.get_job(recommended_job.job_id, workspace_id=workspace_id)
    company = (
        await companies_service.get_company(job.company_id, workspace_id=workspace_id)
        if job is not None
        else None
    )
    user = await users_service.get_user(recommended_job.assessed_by)

    if data.status is not None and recommended_job.status != previous_status:
        await audit_logs_service.record_audit(
            workspace_id=workspace_id,
            entity=AuditEntity.REFERRALS,
            entity_label="Referral",
            actor=current_user.fullname,
            action=f"set a referral to {recommended_job.status.value.replace('_', ' ')}",
            icon="send",
            icon_tone=AuditTone.GREEN,
            chip_tone=AuditTone.GREEN,
            records=([job.title] if job else []) + ([company.company_name] if company else []),
        )
    return _to_read(recommended_job, job, company, user)


@router.delete("/{recommended_job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recommended_job(
    recommended_job_id: UUID,
    workspace_id: Annotated[UUID, Depends(get_current_workspace_id)],
) -> None:
    recommended_job = await recommended_jobs_service.get_recommended_job(
        recommended_job_id, workspace_id=workspace_id
    )
    if recommended_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recommended job not found"
        )
    if not await recommended_jobs_service.delete_recommended_job(recommended_job):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete recommended job",
        )
