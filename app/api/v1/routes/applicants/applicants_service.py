import re
from datetime import UTC, datetime
from uuid import UUID

from beanie.operators import Or, RegEx

from .applicants_models import Applicant
from .applicants_schemas import ApplicantCreate, ApplicantPatch


async def get_applicant(applicant_id: UUID) -> Applicant | None:
    return await Applicant.get(applicant_id)


async def create_applicant(data: ApplicantCreate, created_by: UUID) -> Applicant:
    applicant = Applicant(**data.model_dump(), created_by=created_by)
    await applicant.insert()
    return applicant


async def list_applicants(
    limit: int, offset: int, q: str | None = None
) -> tuple[list[Applicant], int]:
    query = Applicant.find_all()
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
