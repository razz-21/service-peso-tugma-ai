import re
from datetime import UTC, datetime
from uuid import UUID

from beanie.operators import Or, RegEx

from .applicants_models import Applicant
from .applicants_schemas import ApplicantCreate, ApplicantPatch


async def get_applicant(applicant_id: UUID, workspace_id: UUID) -> Applicant | None:
    # Scoped to the workspace: an applicant belonging to another tenant resolves
    # to None, so callers surface it as a 404 rather than leaking cross-workspace data.
    return await Applicant.find_one(
        Applicant.id == applicant_id, Applicant.workspace_id == workspace_id
    )


async def create_applicant(
    data: ApplicantCreate, created_by: UUID, workspace_id: UUID
) -> Applicant:
    applicant = Applicant(**data.model_dump(), created_by=created_by, workspace_id=workspace_id)
    await applicant.insert()
    return applicant


async def list_applicants(
    limit: int, offset: int, workspace_id: UUID, q: str | None = None
) -> tuple[list[Applicant], int]:
    query = Applicant.find(Applicant.workspace_id == workspace_id)
    if q is not None:
        pattern = re.escape(q)
        query = query.find(
            Or(
                RegEx(Applicant.firstname, pattern, "i"),
                RegEx(Applicant.lastname, pattern, "i"),
                RegEx(Applicant.email_address, pattern, "i"),
            )
        )
    total = await query.count()
    applicants = await query.sort("-created_at").skip(offset).limit(limit).to_list()
    return applicants, total


async def update_applicant(applicant: Applicant, data: ApplicantPatch) -> Applicant:
    # Assign the validated (typed) values, not the dumped dicts, so embedded
    # documents and arrays keep their model types.
    changes = data.model_dump(exclude_unset=True)
    for field in changes:
        setattr(applicant, field, getattr(data, field))
    applicant.updated_at = datetime.now(UTC).isoformat()
    await applicant.save()
    return applicant


async def delete_applicant(applicant: Applicant) -> bool:
    # Cascade: remove the applicant's dependent records (recommendations and
    # applicant-job referrals, both keyed by `applicant_id`) before deleting the
    # applicant, so no orphaned rows are left behind. Scoped to the applicant's
    # workspace to stay within the tenant. Imported lazily to avoid a circular
    # import: recommended_jobs' routes import this slice.
    from app.api.v1.routes.applicant_jobs.applicant_jobs_models import ApplicantJob
    from app.api.v1.routes.recommended_jobs.recommended_jobs_models import RecommendedJob

    await RecommendedJob.find(
        RecommendedJob.applicant_id == applicant.id,
        RecommendedJob.workspace_id == applicant.workspace_id,
    ).delete()
    await ApplicantJob.find(
        ApplicantJob.applicant_id == applicant.id,
        ApplicantJob.workspace_id == applicant.workspace_id,
    ).delete()
    result = await applicant.delete()
    return result is not None and result.acknowledged
