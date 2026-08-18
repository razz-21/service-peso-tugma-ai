import re
from datetime import UTC, datetime
from uuid import UUID

from beanie.operators import Or, RegEx

from app.api.v1.routes.files import files_service
from app.matching.extraction import extract_text

from .applicants_extraction import parse_resume
from .applicants_models import Applicant, ApplicantStatus
from .applicants_schemas import ApplicantCreate, ApplicantPatch, ResumeExtraction


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
    limit: int,
    offset: int,
    workspace_id: UUID,
    q: str | None = None,
    status: ApplicantStatus | None = None,
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
    if status is not None:
        query = query.find(Applicant.status == status)
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


def extract_resume(pdf_bytes: bytes) -> ResumeExtraction:
    """Acquire text from a resume PDF and parse it into structured fields.

    Stateless — used by `POST /applicants/extract` to prefill the create form.
    Raises `app.matching.extraction.ExtractionError` for corrupt/encrypted PDFs.
    """
    raw_text, meta = extract_text(pdf_bytes)
    return parse_resume(raw_text, meta)


async def add_applicant_file(
    applicant: Applicant,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    resume_text: str | None,
    uploaded_by: UUID | None = None,
) -> Applicant:
    """Persist an uploaded resume for the applicant and record its raw text.

    The bytes are stored in the generic `files` collection linked to the
    applicant via `foreign_id` (so attachments live in one place across the app,
    not embedded per-record), and `resume_text` — fed into the matcher's semantic
    vector — is set on the applicant when text was extracted.
    """
    await files_service.create_file(
        foreign_id=applicant.id,
        workspace_id=applicant.workspace_id,
        filename=filename,
        content_type=content_type,
        data=data,
        uploaded_by=uploaded_by,
    )
    if resume_text:
        applicant.resume_text = resume_text
    applicant.updated_at = datetime.now(UTC).isoformat()
    await applicant.save()
    return applicant


async def delete_applicant(applicant: Applicant) -> bool:
    # Cascade: remove the applicant's dependent recommendation records (keyed by
    # `applicant_id`) before deleting the applicant, so no orphaned rows are left
    # behind. Scoped to the applicant's workspace to stay within the tenant.
    # Imported lazily to avoid a circular import: recommended_jobs' routes import
    # this slice.
    from app.api.v1.routes.recommended_jobs.recommended_jobs_models import RecommendedJob

    await RecommendedJob.find(
        RecommendedJob.applicant_id == applicant.id,
        RecommendedJob.workspace_id == applicant.workspace_id,
    ).delete()
    # Remove stored files (blobs + metadata) linked to this applicant.
    await files_service.delete_files_for(applicant.id, applicant.workspace_id)
    result = await applicant.delete()
    return result is not None and result.acknowledged
