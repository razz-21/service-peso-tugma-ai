import re
from datetime import UTC, datetime
from uuid import UUID

from beanie.operators import Or, RegEx

from app.core.blob import delete_avatar_blob, replace_avatar_blob

from .companies_models import Company
from .companies_schemas import (
    CompanyApplicantApplicant,
    CompanyApplicantRead,
    CompanyCreate,
    CompanyPatch,
)


async def get_company(company_id: UUID, workspace_id: UUID) -> Company | None:
    # Scoped to the workspace: a company belonging to another tenant resolves to
    # None, so callers surface it as a 404 rather than leaking cross-workspace data.
    return await Company.find_one(Company.id == company_id, Company.workspace_id == workspace_id)


async def create_company(data: CompanyCreate, workspace_id: UUID) -> Company:
    company = Company(**data.model_dump(), workspace_id=workspace_id)
    await company.insert()
    return company


async def list_companies(
    limit: int, offset: int, workspace_id: UUID, q: str | None = None
) -> tuple[list[Company], int]:
    query = Company.find(Company.workspace_id == workspace_id)
    if q is not None:
        pattern = re.escape(q)
        query = query.find(
            Or(
                RegEx(Company.company_name, pattern, "i"),
                RegEx(Company.description, pattern, "i"),
                RegEx(Company.address, pattern, "i"),
            )
        )
    total = await query.count()
    companies = await query.sort("-created_at").skip(offset).limit(limit).to_list()
    return companies, total


async def update_company(company: Company, data: CompanyPatch) -> Company:
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(company, field, value)
    await company.save()
    return company


async def set_company_avatar(company: Company, *, extension: str, data: bytes) -> Company:
    """Upload a new avatar image and store its Blob URL on the company."""
    company.avatar = await replace_avatar_blob(
        prefix="companies",
        entity_id=company.id,
        extension=extension,
        data=data,
        previous_url=company.avatar,
    )
    company.updated_at = datetime.now(UTC).isoformat()
    await company.save()
    return company


async def clear_company_avatar(company: Company) -> Company:
    """Remove the company's avatar, deleting the stored Blob best-effort."""
    await delete_avatar_blob(company.avatar)
    company.avatar = None
    company.updated_at = datetime.now(UTC).isoformat()
    await company.save()
    return company


async def delete_company(company: Company) -> bool:
    result = await company.delete()
    return result is not None and result.acknowledged


async def list_company_applicants(
    company_id: UUID,
    workspace_id: UUID,
    limit: int,
    offset: int,
) -> tuple[list[CompanyApplicantRead], int]:
    """List the applicants referred to a company's jobs (its "Applicants" table).

    Resolves the company → its jobs → referrals → applicants chain: a referral is
    a `recommended_jobs` row with a lifecycle status set (an officer acted on the
    recommendation), so untouched AI recommendations are excluded. Ordered by most
    recently referred, and paginated over the referral rows.

    The recommended_jobs / applicants / jobs models are imported lazily: the
    recommended_jobs slice imports this (companies) service, so importing its
    model at module load would create an import cycle (mirrors the lazy import in
    jobs_service.count_job_referrals).
    """
    from beanie.operators import NE, In

    from ..applicants.applicants_models import Applicant
    from ..jobs.jobs_models import Job
    from ..recommended_jobs.recommended_jobs_models import RecommendedJob

    # The company's jobs — the only jobs a referral to this company can point at.
    jobs = await Job.find(Job.company_id == company_id, Job.workspace_id == workspace_id).to_list()
    if not jobs:
        return [], 0
    job_titles = {job.id: job.title for job in jobs}

    query = RecommendedJob.find(
        In(RecommendedJob.job_id, list(job_titles.keys())),
        RecommendedJob.workspace_id == workspace_id,
        # Referrals only: a lifecycle status is set. Excludes the untouched,
        # auto-generated recommendations (status is null).
        NE(RecommendedJob.status, None),
    )
    total = await query.count()
    referrals = await query.sort("-referred_at").skip(offset).limit(limit).to_list()

    # Batch-resolve the referred applicants for this page in one query (no N+1).
    applicant_ids = list({r.applicant_id for r in referrals if r.applicant_id is not None})
    applicants = (
        await Applicant.find(
            In(Applicant.id, applicant_ids), Applicant.workspace_id == workspace_id
        ).to_list()
        if applicant_ids
        else []
    )
    applicants_map = {applicant.id: applicant for applicant in applicants}

    items: list[CompanyApplicantRead] = []
    for referral in referrals:
        # NE(status, None) above guarantees a status is set; skip defensively so
        # the read's non-optional `status` is never fed a null.
        if referral.status is None:
            continue
        applicant = (
            applicants_map.get(referral.applicant_id) if referral.applicant_id is not None else None
        )
        applicant_summary = (
            CompanyApplicantApplicant(
                id=applicant.id,
                name=" ".join(filter(None, (applicant.firstname, applicant.lastname))).strip(),
            )
            if applicant is not None
            else None
        )
        items.append(
            CompanyApplicantRead(
                id=referral.id,
                applicant=applicant_summary,
                referred_to=job_titles.get(referral.job_id),
                job_id=referral.job_id,
                status=referral.status.value,
                date_referred=referral.referred_at,
            )
        )
    return items, total
