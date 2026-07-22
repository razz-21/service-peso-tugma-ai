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
    result = await applicant.delete()
    return result is not None and result.acknowledged
