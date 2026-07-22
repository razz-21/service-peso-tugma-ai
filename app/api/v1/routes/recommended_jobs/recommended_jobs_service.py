from uuid import UUID

from beanie.operators import In

from app.api.v1.routes.applicants.applicants_models import Applicant
from app.api.v1.routes.workspaces.workspaces_models import Workspace
from app.matching.embeddings import embed, embed_batch
from app.matching.profile import (
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
    location_match,
    parse_required_years,
    skills_match,
)

from ..jobs.jobs_models import Job, JobStatus
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


async def update_recommended_job(
    recommended_job: RecommendedJob, data: RecommendedJobPatch
) -> RecommendedJob:
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(recommended_job, field, value)
    await recommended_job.save()
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
    Regenerating replaces the applicant's recommendations wholesale: all existing
    rows for the applicant are deleted first, then the fresh Top-K is inserted (so
    any prior Human-in-the-Loop assessments are discarded). Returns the
    recommendations in rank order.
    """
    workspace_id = workspace.id
    weights = _weights_for(workspace)

    # Applicant-side features (computed once, reused across all jobs).
    applicant_vec = embed(applicant_to_text(applicant))
    applicant_years = applicant_experience_years(applicant)
    highest_education = (
        applicant.educational_background.highest_education_level
        if applicant.educational_background is not None
        else None
    )

    jobs = await Job.find(
        Job.workspace_id == workspace_id, Job.status == JobStatus.ACTIVE
    ).to_list()
    if not jobs:
        return []
    await _ensure_job_embeddings(jobs)

    scored: list[tuple[Job, RecommendationScores, int, list[str]]] = []
    for job in jobs:
        semantic = cosine_similarity(applicant_vec, job.embedding)
        skills, matched_skills = skills_match(applicant.technical_skills, job.skills_required)
        experience, experience_reason = experience_match(
            applicant_years, parse_required_years(job.experience_required)
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

    # Regenerate = replace: drop every existing recommendation for this applicant
    # in the workspace, then insert the fresh Top-K.
    await RecommendedJob.find(
        RecommendedJob.applicant_id == applicant.id,
        RecommendedJob.workspace_id == workspace_id,
    ).delete()

    results: list[RecommendedJob] = []
    for job, stored_scores, final_pct, key_matched in top:
        rec = RecommendedJob(
            job_id=job.id,
            applicant_id=applicant.id,
            scores=stored_scores,
            score=final_pct,
            embedded_applicant=applicant_vec,
            embedded_job=job.embedding,
            key_matched=key_matched,
            assessed_by=assessed_by,
            workspace_id=workspace_id,
        )
        await rec.insert()
        results.append(rec)

    return results
