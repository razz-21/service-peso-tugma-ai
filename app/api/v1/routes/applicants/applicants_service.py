import asyncio
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import vercel_blob
from beanie.operators import Or, RegEx

from app.matching.extraction import extract_text

from .applicants_extraction import parse_resume
from .applicants_models import Applicant, ApplicantFile, ApplicantStatus
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
    limit: int, offset: int, workspace_id: UUID, q: str | None = None, status: ApplicantStatus | None = None
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
) -> Applicant:
    """Persist an uploaded file for the applicant and record the raw resume text.

    Uploads the bytes to Vercel Blob at `applicants/<applicant_id>/<file_id>.pdf`
    (the serverless filesystem is ephemeral/read-only), appends an embedded
    `ApplicantFile` whose `storage_ref` is the returned Blob URL, and sets
    `resume_text` (fed into the matcher's semantic vector) when text was extracted.
    """
    file_id = uuid4()
    pathname = f"applicants/{applicant.id}/{file_id}.pdf"
    # vercel_blob.put is synchronous (requests-based); run it off the event loop.
    # The store's BLOB_READ_WRITE_TOKEN is read from the environment.
    result = await asyncio.to_thread(
        vercel_blob.put,
        pathname,
        data,
        {"addRandomSuffix": "false"},
    )
    applicant.files.append(
        ApplicantFile(
            id=file_id,
            filename=filename,
            size=len(data),
            content_type=content_type,
            storage_ref=result["url"],
        )
    )
    if resume_text:
        applicant.resume_text = resume_text
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
