import hashlib
from datetime import UTC, datetime
from uuid import UUID

from beanie.operators import NE, Eq, In

from app.api.v1.routes.applicants.applicants_models import Applicant
from app.api.v1.routes.workspaces.workspaces_models import Workspace
from app.core.config import settings
from app.matching.embeddings import embed, embed_batch
from app.matching.preprocessing import strip_degree_framing
from app.matching.primary_requirements import eligibility_matches, meets_primary_requirements
from app.matching.profile import (
    applicant_course_text,
    applicant_experience_text,
    applicant_experience_years,
    applicant_to_text,
    job_course_text,
    job_to_text,
)
from app.matching.scoring import (
    MatchWeights,
    ScoreBreakdown,
    classify_skill_matches,
    combined_score,
    cosine_similarity,
    education_mandatory_met,
    education_match,
    experience_mandatory_met,
    experience_match,
    experience_requirement_terms,
    location_match,
    parse_required_years,
    skills_mandatory_covered,
    skills_match,
)

from ..jobs.jobs_models import Job, JobStatus
from ..users.users_models import User
from .recommended_jobs_models import (
    RecommendationScores,
    RecommendedJob,
    RecommendedJobStatus,
    SkillMatch,
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
    referred: bool | None = None,
) -> tuple[list[RecommendedJob], int]:
    query = RecommendedJob.find(RecommendedJob.workspace_id == workspace_id)
    if job_id is not None:
        query = query.find(RecommendedJob.job_id == job_id)
    if applicant_id is not None:
        query = query.find(RecommendedJob.applicant_id == applicant_id)
    if status is not None:
        query = query.find(RecommendedJob.status == status)
    # Referral split: `referred=True` returns only rows an officer has acted on
    # (a lifecycle status is set), `referred=False` only the untouched AI
    # recommendations (status is null). Lets the client fetch the "Recommended"
    # list and the "Referred" list as two disjoint server-side queries instead of
    # slicing one combined response by status on the frontend.
    if referred is True:
        query = query.find(NE(RecommendedJob.status, None))
    elif referred is False:
        query = query.find(Eq(RecommendedJob.status, None))
    if is_relevant is not None:
        query = query.find(RecommendedJob.is_relevant == is_relevant)
    if assessed_by is not None:
        query = query.find(RecommendedJob.assessed_by == assessed_by)
    total = await query.count()
    recommendations = await query.sort("-date_registered").skip(offset).limit(limit).to_list()
    return recommendations, total


# Referral-lifecycle statuses in which the applicant still occupies one of the
# job's vacancies. Moving *into* one of these consumes a vacancy; moving *out*
# of them (to withdrawn / not_hired / resigned, or back to unassessed) releases
# it. `hired` keeps the seat consumed — the position stays filled until the
# applicant later resigns, which frees it again.
_VACANCY_HOLDING_STATUSES = frozenset(
    {
        RecommendedJobStatus.REFERRED,
        RecommendedJobStatus.INTERVIEW_SCHEDULED,
        RecommendedJobStatus.HIRED,
    }
)


# Terminal lifecycle statuses: once a referral reaches one of these the outcome
# is final and the status can no longer change. `resigned` is additionally
# reachable only from `hired` (see `status_transition_error`).
_TERMINAL_STATUSES = frozenset(
    {
        RecommendedJobStatus.WITHDRAWN,
        RecommendedJobStatus.NOT_HIRED,
        RecommendedJobStatus.RESIGNED,
    }
)


def status_transition_error(
    previous: RecommendedJobStatus | None,
    new: RecommendedJobStatus,
) -> str | None:
    """A human-readable reason the status transition is disallowed, else None.

    Enforces the referral lifecycle server-side (the UI gates the same rules):

    * A terminal status (withdrawn / not_hired / resigned) is final — no further
      change is permitted.
    * `resigned` is reachable only from `hired` — an applicant can't resign a
      position they were never placed in.

    Re-applying the current status is a no-op and always allowed.
    """
    if previous == new:
        return None
    if previous in _TERMINAL_STATUSES:
        return f"This referral is {previous.value.replace('_', ' ')} and can no longer be updated"
    if new == RecommendedJobStatus.RESIGNED and previous != RecommendedJobStatus.HIRED:
        return "An applicant can only be marked resigned after being hired"
    return None


def _holds_vacancy(status: RecommendedJobStatus | None) -> bool:
    return status in _VACANCY_HOLDING_STATUSES


def starts_holding_vacancy(
    previous_status: RecommendedJobStatus | None,
    new_status: RecommendedJobStatus | None,
) -> bool:
    """Whether a status transition moves a recommendation *into* a seat-holding
    state (e.g. referring the applicant), which consumes one of the job's
    vacancies. Callers gate such a transition on an open vacancy still existing;
    advancing between two holding states, or releasing a seat, returns False.
    """
    return not _holds_vacancy(previous_status) and _holds_vacancy(new_status)


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


async def _refresh_job_embeddings(jobs: list[Job]) -> None:
    # Keep each job's cached `Job.embedding` in sync with its *current* text. A job
    # is re-embedded when it has no vector yet, or when its text changed since the
    # vector was computed (an officer edited the requirements) — detected by
    # hashing the text and comparing to the stored `embedding_source`. Unchanged
    # jobs are skipped, so we never re-encode or re-save a job needlessly; the
    # stale ones are re-encoded together in a single batched call.
    stale: list[tuple[Job, str]] = []  # (job, current signature)
    for job in jobs:
        signature = hashlib.sha256(job_to_text(job).encode("utf-8")).hexdigest()
        if not job.embedding or job.embedding_source != signature:
            stale.append((job, signature))
    if not stale:
        return
    vectors = embed_batch([job_to_text(job) for job, _ in stale])
    for (job, signature), vector in zip(stale, vectors, strict=True):
        job.embedding = vector
        job.embedding_source = signature
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
    the applicant has already been referred to (status set) are preserved in the
    database (their referral history/state survives), and their jobs are excluded
    from the fresh Top-K so they are never re-recommended. Returns only the fresh
    *unreferred* Top-K in rank order — already-referred jobs are intentionally left
    out, so the caller's Recommended list never contains a job under referral.
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
    # Embedding of the applicant's course/program (field of study), for the
    # education dimension's course match. None when no course is on file — a
    # job's course requirement then scores 0 (no evidence).
    applicant_course = applicant_course_text(applicant)
    applicant_course_vec = embed(applicant_course) if applicant_course else None
    # Applicant skill vectors (once) for the semantic skills match: a required
    # skill is credited by an exact match or a sufficiently similar applicant
    # skill (e.g. "JS" for "JavaScript"), embedded with the same MiniLM model.
    applicant_skill_names = [skill.strip() for skill in applicant.technical_skills if skill.strip()]
    applicant_skill_vecs = embed_batch(applicant_skill_names) if applicant_skill_names else []
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
        # No fresh candidates: clear the stale (unreferred) rows. The preserved
        # referrals stay in the database but are not part of the Recommended list,
        # so nothing fresh is returned.
        if stale_ids:
            await RecommendedJob.find(In(RecommendedJob.id, stale_ids)).delete()
        return []

    await _refresh_job_embeddings(jobs)

    # Embed each job's qualitative experience requirement (field/role wording) in
    # one batch, so the per-job loop can cosine-compare it to the applicant's
    # experience without re-encoding. Degree framing is stripped so a field-of-
    # study requirement contrasts the disciplines ("Logistics" vs "Information
    # Technology") rather than the shared "Bachelor of ... Degree in ..."
    # scaffolding that inflates any two diplomas' similarity. Jobs with a purely
    # numeric requirement, or a bare degree *level* (which strips to nothing and
    # is left to education_match), have no qualitative term and are skipped.
    job_qual_raw = {job.id: experience_requirement_terms(job.experience_required) for job in jobs}
    job_qual_terms = {
        job_id: (strip_degree_framing(raw) or None) if raw else None
        for job_id, raw in job_qual_raw.items()
    }
    qual_jobs = [job for job in jobs if job_qual_terms[job.id]]
    qual_vectors = embed_batch([job_qual_terms[job.id] or "" for job in qual_jobs])
    job_qual_vec = {job.id: vec for job, vec in zip(qual_jobs, qual_vectors, strict=True)}

    # Embed each job's preferred course/program (field-of-study wording) in one
    # batch, so the per-job loop can cosine-compare it to the applicant's course
    # for the education dimension without re-encoding. Degree framing is stripped
    # (see job_course_text) so disciplines contrast rather than shared diploma
    # scaffolding. Jobs that name no course are skipped.
    job_course_terms = {job.id: job_course_text(job) for job in jobs}
    course_jobs = [job for job in jobs if job_course_terms[job.id]]
    course_vectors = embed_batch([job_course_terms[job.id] for job in course_jobs])
    job_course_vec = {job.id: vec for job, vec in zip(course_jobs, course_vectors, strict=True)}

    # Embed the unique required *and preferred* skills across all candidate jobs
    # once, so the per-job loop can cosine-match each against the applicant's
    # skills (for the semantic skills score) without re-encoding. Preferred skills
    # are embedded too so the nice-to-have tier gets the same semantic matching.
    # Only needed when the applicant lists skills; otherwise the skills match
    # falls back to exact tokens.
    required_skill_vecs: dict[str, list[float]] = {}
    if applicant_skill_vecs:
        unique_required = sorted(
            {
                skill.strip()
                for job in jobs
                for skill in (*job.skills_required, *job.preferred_skills)
                if skill.strip()
            }
        )
        if unique_required:
            skill_vectors = embed_batch(unique_required)
            required_skill_vecs = dict(zip(unique_required, skill_vectors, strict=True))

    scored: list[tuple[Job, RecommendationScores, int, list[str], list[SkillMatch]]] = []
    for job in jobs:
        semantic = cosine_similarity(applicant_vec, job.embedding)
        # Best cosine of each required skill against the applicant's skills, for
        # the semantic (MiniLM) skills match. None when the applicant lists no
        # skills — skills_match then falls back to exact-token matching.
        skill_similarities: dict[str, float] | None = None
        # Best-matching applicant skill (argmax) per required skill, so the
        # compare modal can show which skill covers each requirement and how
        # closely (e.g. "Excel · via Google Sheets").
        skill_sources: dict[str, tuple[str | None, float]] = {}
        if applicant_skill_vecs and required_skill_vecs:
            skill_similarities = {}
            # Both tiers are matched semantically against the applicant's skills.
            for skill in (*job.skills_required, *job.preferred_skills):
                key = skill.strip()
                required_vec = required_skill_vecs.get(key)
                if required_vec is None:
                    continue
                best_name: str | None = None
                best_cosine = 0.0
                for name, av in zip(applicant_skill_names, applicant_skill_vecs, strict=True):
                    cosine = cosine_similarity(required_vec, av)
                    if cosine > best_cosine:
                        best_cosine = cosine
                        best_name = name
                skill_similarities[key] = best_cosine
                skill_sources[key] = (best_name, best_cosine)
        skills, matched_skills = skills_match(
            applicant.technical_skills,
            job.skills_required,
            skill_similarities,
            job.preferred_skills,
            bonus_cap=settings.REQUIREMENT_BONUS_CAP,
        )
        skill_matches = [
            SkillMatch(
                required=match.required,
                applicant=match.applicant,
                similarity=round(match.similarity * 100),
                state=match.state,
                tier=match.tier,
            )
            for match in classify_skill_matches(
                applicant.technical_skills,
                job.skills_required,
                skill_sources or None,
                job.preferred_skills,
            )
        ]
        qualitative_similarity: float | None = None
        if job_qual_terms[job.id] is not None:
            qual_vec = job_qual_vec.get(job.id)
            qualitative_similarity = (
                cosine_similarity(applicant_experience_vec, qual_vec)
                if applicant_experience_vec is not None and qual_vec is not None
                else 0.0
            )
        required_years = parse_required_years(job.experience_required)
        experience, experience_reason = experience_match(
            applicant_years,
            required_years,
            qualitative_similarity,
            is_preferred=job.experience_is_preferred,
            bonus_cap=settings.REQUIREMENT_BONUS_CAP,
        )
        course_similarity: float | None = None
        if job_course_terms[job.id]:
            course_vec = job_course_vec.get(job.id)
            course_similarity = (
                cosine_similarity(applicant_course_vec, course_vec)
                if applicant_course_vec is not None and course_vec is not None
                else 0.0
            )
        education, education_reason = education_match(
            highest_education,
            job.minimum_education_attainment,
            course_similarity,
            job.preferred_education,
            bonus_cap=settings.REQUIREMENT_BONUS_CAP,
        )
        # Optional hard-gate: when GATE_ON_MANDATORY is enabled, exclude the job
        # entirely if the applicant fails any mandatory (must-have) requirement,
        # alongside the primary-requirement gates applied before scoring. Preferred
        # tiers never gate. Off by default — the soft penalty (a missing must-have
        # caps the criterion below 1 - bonus_cap) applies instead.
        if settings.GATE_ON_MANDATORY and not (
            skills_mandatory_covered(
                applicant.technical_skills, job.skills_required, skill_similarities
            )
            and education_mandatory_met(
                highest_education, job.minimum_education_attainment, course_similarity
            )
            and (
                job.experience_is_preferred
                or experience_mandatory_met(applicant_years, required_years, qualitative_similarity)
            )
        ):
            continue
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
        scored.append((job, stored_scores, _to_pct(final), key_matched, skill_matches))

    scored.sort(key=lambda item: item[2], reverse=True)
    top = scored[:top_k]

    # Regenerate = replace only the unreferred recommendations; the referred rows
    # in `preserved` are kept so referral history/state survives a regenerate.
    if stale_ids:
        await RecommendedJob.find(In(RecommendedJob.id, stale_ids)).delete()

    results: list[RecommendedJob] = []
    for job, stored_scores, final_pct, key_matched, skill_matches in top:
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
            skill_matches=skill_matches,
            assessed_by=assessed_by,
            workspace_id=workspace_id,
        )
        await rec.insert()
        results.append(rec)

    # Return only the fresh, unreferred Top-K in rank order. The preserved
    # referrals remain persisted (and their jobs were excluded above) but are
    # deliberately omitted here, so the Recommended list holds no referred job.
    return sorted(results, key=lambda rec: rec.score, reverse=True)
