from datetime import UTC, datetime
from uuid import UUID

from beanie.operators import In

from app.api.v1.routes.applicants.applicants_models import Applicant
from app.api.v1.routes.workspaces.workspaces_models import Workspace
from app.matching.embeddings import embed, embed_batch
from app.matching.preprocessing import preprocess
from app.matching.primary_requirements import eligibility_matches, meets_primary_requirements
from app.matching.profile import (
    applicant_experience_text,
    applicant_experience_years,
    applicant_to_text,
    job_to_text,
)
from app.matching.scoring import (
    MatchWeights,
    ScoreBreakdown,
    combined_score,
    cosine_similarity,
    education_match,
    experience_match,
    experience_requirement_terms,
    location_match,
    parse_required_years,
    skills_match,
)

from ..jobs.jobs_models import Job, JobStatus
from ..users.users_models import User
from .recommended_jobs_models import (
    RecommendationScores,
    RecommendedJob,
    RecommendedJobStatus,
)
from .recommended_jobs_schemas import RecommendedJobCreate, RecommendedJobPatch


def _to_pct(value: float) -> int:
    """Convert a normalized [0, 1] score into a stored 0-100 integer."""
    return int(round(max(0.0, min(1.0, value)) * 100))


async def get_recommended_job(
    recommended_job_id: UUID, workspace_id: UUID
) -> RecommendedJob | None:
    # Scoped to the workspace: a record belonging to another tenant resolves to
    # None, so callers surface it as a 404 rather than leaking cross-workspace data.
    return await RecommendedJob.find_one(
        RecommendedJob.id == recommended_job_id,
        RecommendedJob.workspace_id == workspace_id,
    )


async def get_jobs_map(
    recommendations: list[RecommendedJob], workspace_id: UUID
) -> dict[UUID, Job]:
    # Batch-resolve the `job_id` foreign keys for a page of recommendations in a
    # single query, keyed by id, so read endpoints can embed the job without N+1.
    ids = list({rec.job_id for rec in recommendations})
    if not ids:
        return {}
    jobs = await Job.find(In(Job.id, ids), Job.workspace_id == workspace_id).to_list()
    return {job.id: job for job in jobs}


async def get_users_map(recommendations: list[RecommendedJob]) -> dict[UUID, User]:
    # Batch-resolve the `assessed_by` foreign keys (the assessor/referrer) for a
    # page of recommendations. Users are not workspace-scoped.
    ids = list({rec.assessed_by for rec in recommendations})
    if not ids:
        return {}
    users = await User.find(In(User.id, ids)).to_list()
    return {user.id: user for user in users}


async def create_recommended_job(
    data: RecommendedJobCreate, workspace_id: UUID, assessed_by: UUID
) -> RecommendedJob:
    recommended_job = RecommendedJob(
        **data.model_dump(exclude={"assessed_by"}),
        assessed_by=assessed_by,
        workspace_id=workspace_id,
    )
    await recommended_job.insert()
    return recommended_job


async def list_recommended_jobs(
    limit: int,
    offset: int,
    workspace_id: UUID,
    job_id: UUID | None = None,
    applicant_id: UUID | None = None,
    status: RecommendedJobStatus | None = None,
    is_relevant: bool | None = None,
    assessed_by: UUID | None = None,
) -> tuple[list[RecommendedJob], int]:
    query = RecommendedJob.find(RecommendedJob.workspace_id == workspace_id)
    if job_id is not None:
        query = query.find(RecommendedJob.job_id == job_id)
    if applicant_id is not None:
        query = query.find(RecommendedJob.applicant_id == applicant_id)
    if status is not None:
        query = query.find(RecommendedJob.status == status)
    if is_relevant is not None:
        query = query.find(RecommendedJob.is_relevant == is_relevant)
    if assessed_by is not None:
        query = query.find(RecommendedJob.assessed_by == assessed_by)
    total = await query.count()
    recommendations = await query.sort("-date_registered").skip(offset).limit(limit).to_list()
    return recommendations, total


# Referral-lifecycle statuses in which the applicant still occupies one of the
# job's vacancies. Moving *into* one of these consumes a vacancy; moving *out*
# of them (to withdrawn / not_hired, or back to unassessed) releases it. `hired`
# keeps the seat consumed — the position stays filled.
_VACANCY_HOLDING_STATUSES = frozenset(
    {
        RecommendedJobStatus.REFERRED,
        RecommendedJobStatus.INTERVIEW_SCHEDULED,
        RecommendedJobStatus.HIRED,
    }
)


def _holds_vacancy(status: RecommendedJobStatus | None) -> bool:
    return status in _VACANCY_HOLDING_STATUSES


async def _apply_vacancy_delta(job_id: UUID, workspace_id: UUID, delta: int) -> None:
    """Adjust a job's open-vacancy count when a referral occupies/releases a seat.

    `delta` is -1 when a referral starts holding a vacancy (e.g. the applicant is
    referred) and +1 when it releases one (withdrawn / not_hired). The count is
    floored at 0 so a duplicate or out-of-order transition can never drive it
    negative. A missing job (deleted out from under the referral) is a no-op.
    """
    job = await Job.find_one(Job.id == job_id, Job.workspace_id == workspace_id)
    if job is None:
        return
    job.no_of_vacancies = max(0, job.no_of_vacancies + delta)
    await job.save()


async def update_recommended_job(
    recommended_job: RecommendedJob, data: RecommendedJobPatch
) -> RecommendedJob:
    changes = data.model_dump(exclude_unset=True)
    # `updated_at` is server-owned — always stamp it fresh so any edit (e.g. an
    # officer referring the applicant) bumps the timestamp, keeping the most
    # recently touched referral sorted to the top for clients.
    changes.pop("updated_at", None)
    # Capture the status before applying changes so a status transition can
    # occupy or release one of the job's vacancies (referring the applicant
    # consumes a seat; withdrawing / not-hiring rolls it back).
    previous_status = recommended_job.status
    for field, value in changes.items():
        setattr(recommended_job, field, value)
    recommended_job.updated_at = datetime.now(UTC).isoformat()
    await recommended_job.save()

    if "status" in changes:
        was_holding = _holds_vacancy(previous_status)
        now_holding = _holds_vacancy(recommended_job.status)
        if was_holding != now_holding:
            await _apply_vacancy_delta(
                recommended_job.job_id,
                recommended_job.workspace_id,
                delta=-1 if now_holding else 1,
            )

    return recommended_job


async def delete_recommended_job(recommended_job: RecommendedJob) -> bool:
    result = await recommended_job.delete()
    return result is not None and result.acknowledged


def _weights_for(workspace: Workspace) -> MatchWeights:
    # Map the workspace's configurable percentage weights onto the pipeline's
    # fractional weights (paper default profile: 50/20/15/10/5).
    ms = workspace.matching_score
    return MatchWeights.from_percent(
        semantic_similarity=ms.semantic_similarity,
        skills=ms.skills_match,
        experience=ms.experience_match,
        education=ms.educational_match,
        location=ms.location_preference,
    )


async def _ensure_job_embeddings(jobs: list[Job]) -> None:
    # Populate and persist the cached `Job.embedding` for any job missing one, in
    # a single batched encode, so repeat runs reuse the stored vectors.
    missing = [job for job in jobs if not job.embedding]
    if not missing:
        return
    vectors = embed_batch([job_to_text(job) for job in missing])
    for job, vector in zip(missing, vectors, strict=True):
        job.embedding = vector
        await job.save()


async def generate_recommendations(
    applicant: Applicant,
    workspace: Workspace,
    assessed_by: UUID,
    top_k: int = 5,
) -> list[RecommendedJob]:
    """Run the AI pipeline over the workspace's active jobs and persist the Top-K.

    Embeds the applicant profile, scores every active job (semantic cosine +
    rule-based skills/experience/education/location) with the workspace's weights,
    ranks by the combined MatchScore, and stores the Top-K as RecommendedJob rows.
    Regenerating replaces only the applicant's *unreferred* recommendations: rows
    the applicant has already been referred to (status set) are preserved, and
    their jobs are excluded from the fresh Top-K so they are never duplicated.
    Returns every current recommendation (preserved + fresh) in rank order.
    """
    workspace_id = workspace.id
    weights = _weights_for(workspace)

    # Applicant-side features (computed once, reused across all jobs). The raw
    # text of the uploaded resume (if any) is folded into the semantic vector so
    # the recommendation is grounded in the uploaded file.
    applicant_vec = embed(applicant_to_text(applicant, applicant.resume_text))
    applicant_years = applicant_experience_years(applicant)
    applicant_experience = applicant_experience_text(applicant)
    # Embedding of the applicant's roles/qualifications, for the qualitative
    # experience match. None when the applicant has no experience/education text
    # on file — a qualitative requirement then scores 0 (no evidence).
    applicant_experience_vec = embed(applicant_experience) if applicant_experience else None
    highest_education = (
        applicant.educational_background.highest_education_level
        if applicant.educational_background is not None
        else None
    )

    # Preserve recommendations the applicant has already been referred to (status
    # set): a regenerate must not discard referral history/state. Only unreferred
    # rows (status is None) are replaced. Referred jobs are also excluded from the
    # fresh candidate pool below, so we never insert a duplicate row for a job the
    # applicant is already referred to.
    existing = await RecommendedJob.find(
        RecommendedJob.applicant_id == applicant.id,
        RecommendedJob.workspace_id == workspace_id,
    ).to_list()
    preserved = [rec for rec in existing if rec.status is not None]
    stale_ids = [rec.id for rec in existing if rec.status is None]
    preserved_job_ids = {rec.job_id for rec in preserved}

    jobs = await Job.find(
        Job.workspace_id == workspace_id, Job.status == JobStatus.ACTIVE
    ).to_list()

    # Hard primary-requirement gate: drop jobs whose primary requirements the
    # applicant categorically fails (no open vacancy, or a specified age range /
    # sex / civil status the applicant fails) before scoring. Also drop jobs the
    # applicant is already referred to, which are kept via `preserved`.
    jobs = [
        job
        for job in jobs
        if job.id not in preserved_job_ids and meets_primary_requirements(applicant, job)
    ]
    if not jobs:
        # No fresh candidates: clear the stale (unreferred) rows and return the
        # preserved referrals in rank order.
        if stale_ids:
            await RecommendedJob.find(In(RecommendedJob.id, stale_ids)).delete()
        return sorted(preserved, key=lambda rec: rec.score, reverse=True)

    await _ensure_job_embeddings(jobs)

    # Embed each job's qualitative experience requirement (field/role wording) in
    # one batch, so the per-job loop can cosine-compare it to the applicant's
    # experience without re-encoding. Jobs with a purely numeric or empty
    # requirement have no qualitative term and are skipped here.
    job_qual_terms = {job.id: experience_requirement_terms(job.experience_required) for job in jobs}
    qual_jobs = [job for job in jobs if job_qual_terms[job.id]]
    qual_vectors = embed_batch([preprocess(job_qual_terms[job.id] or "") for job in qual_jobs])
    job_qual_vec = {job.id: vec for job, vec in zip(qual_jobs, qual_vectors, strict=True)}

    scored: list[tuple[Job, RecommendationScores, int, list[str]]] = []
    for job in jobs:
        semantic = cosine_similarity(applicant_vec, job.embedding)
        skills, matched_skills = skills_match(applicant.technical_skills, job.skills_required)
        qualitative_similarity: float | None = None
        if job_qual_terms[job.id] is not None:
            qual_vec = job_qual_vec.get(job.id)
            qualitative_similarity = (
                cosine_similarity(applicant_experience_vec, qual_vec)
                if applicant_experience_vec is not None and qual_vec is not None
                else 0.0
            )
        experience, experience_reason = experience_match(
            applicant_years,
            parse_required_years(job.experience_required),
            qualitative_similarity,
        )
        education, education_reason = education_match(
            highest_education, job.minimum_education_attainment
        )
        location, location_reason = location_match(applicant.preferred_work_location, job.location)
        breakdown = ScoreBreakdown(
            semantic_similarity=semantic,
            skills=skills,
            experience=experience,
            education=education,
            location=location,
        )
        final = combined_score(breakdown, weights)
        key_matched = [*matched_skills]
        for reason in (experience_reason, education_reason, location_reason):
            if reason:
                key_matched.append(reason)
        stored_scores = RecommendationScores(
            semantic_similarity=_to_pct(semantic),
            skills=_to_pct(skills),
            experience=_to_pct(experience),
            educational_background=_to_pct(education),
            location_preference=_to_pct(location),
        )
        scored.append((job, stored_scores, _to_pct(final), key_matched))

    scored.sort(key=lambda item: item[2], reverse=True)
    top = scored[:top_k]

    # Regenerate = replace only the unreferred recommendations; the referred rows
    # in `preserved` are kept so referral history/state survives a regenerate.
    if stale_ids:
        await RecommendedJob.find(In(RecommendedJob.id, stale_ids)).delete()

    results: list[RecommendedJob] = []
    for job, stored_scores, final_pct, key_matched in top:
        rec = RecommendedJob(
            job_id=job.id,
            applicant_id=applicant.id,
            scores=stored_scores,
            score=final_pct,
            eligible=eligibility_matches(
                getattr(applicant, "eligibility", None), getattr(job, "eligibility", None)
            ),
            embedded_applicant=applicant_vec,
            embedded_job=job.embedding,
            key_matched=key_matched,
            assessed_by=assessed_by,
            workspace_id=workspace_id,
        )
        await rec.insert()
        results.append(rec)

    # Return every current recommendation — preserved referrals plus the fresh
    # Top-K — in rank order, so the client reflects the full, deduplicated set.
    return sorted([*preserved, *results], key=lambda rec: rec.score, reverse=True)
