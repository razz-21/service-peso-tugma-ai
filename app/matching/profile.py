"""Adapters from domain models into job-matching pipeline inputs.

`applicant_to_text` / `job_to_text` produce the preprocessed text fed to the
embedding model (the semantic component). `applicant_experience_years` derives
total work experience for the rule-based experience score. These are the only
matching functions that reach into the Applicant/Job models, keeping `scoring`
free of model coupling.
"""

from datetime import UTC, datetime

from app.api.v1.routes.applicants.applicants_models import Applicant
from app.api.v1.routes.jobs.jobs_models import Job

from .preprocessing import preprocess, strip_degree_framing


def _join(parts: list[str]) -> str:
    return " ".join(part for part in parts if part and part.strip())


def applicant_to_text(applicant: Applicant, resume_text: str | None = None) -> str:
    """Build preprocessed applicant text for semantic embedding.

    Concatenates the resume-derived fields most relevant to matching (skills,
    work experience, education, trainings, preferred occupation/industry) plus
    any raw text extracted from an uploaded resume file, then runs the standard
    preprocessing pipeline.
    """
    parts: list[str] = []
    if applicant.technical_skills:
        parts.append("skills " + " ".join(applicant.technical_skills))
    for experience in applicant.work_experience:
        parts.append(_join([experience.position or "", experience.company or ""]))
    education = applicant.educational_background
    if education is not None:
        parts.append(
            _join(
                [
                    education.highest_education_level or "",
                    education.course_program or "",
                    education.school_university or "",
                ]
            )
        )
    for training in applicant.trainings:
        parts.append(_join([training.training_title or "", training.certificate_received or ""]))
    for preference in applicant.preferred_occupation_industry:
        parts.append(_join([preference.occupation or "", preference.industry or ""]))
    if resume_text:
        parts.append(resume_text)
    return preprocess(_join(parts))


def job_to_text(job: Job) -> str:
    """Build preprocessed job text for semantic embedding."""
    parts: list[str] = [job.title]
    if job.description:
        parts.append(job.description)
    if job.skills_required:
        parts.append("skills " + " ".join(job.skills_required))
    # Preferred skills are folded into the semantic text too, so a nice-to-have
    # skill still contributes to the cosine similarity and editing it triggers a
    # re-embed (see the `embedding_source` fingerprint in the recommender).
    if job.preferred_skills:
        parts.append("skills " + " ".join(job.preferred_skills))
    if job.experience_required:
        parts.append(job.experience_required)
    if job.minimum_education_attainment:
        parts.append(" ".join(job.minimum_education_attainment))
    if job.preferred_education:
        parts.append(" ".join(job.preferred_education))
    if job.course_program:
        parts.append(job.course_program)
    return preprocess(_join(parts))


def applicant_experience_text(applicant: Applicant) -> str:
    """Preprocessed text of the applicant's *work* experience (roles/companies).

    Concatenates work positions and companies — the fields a free-text
    experience requirement (a role or field of work) is matched against
    semantically, so role titles like "Field Engineer" survive. Education and
    course of study are deliberately excluded here: a course requirement is
    scored by the education dimension instead (see ``education_match`` and
    ``applicant_course_text``), keeping experience about work history alone.
    Empty when the applicant has no work history, which the caller treats as
    "no evidence" for a qualitative experience requirement.
    """
    work_parts = [
        _join([experience.position or "", experience.company or ""])
        for experience in applicant.work_experience
    ]
    return preprocess(_join(work_parts))


def applicant_course_text(applicant: Applicant) -> str:
    """Field-of-study text for the applicant's course/program, for the education
    dimension's course match.

    Degree framing is stripped (so "Bachelor of Science in Information
    Technology" contrasts as "Information Technology") to compare disciplines
    rather than shared diploma scaffolding. Empty when no course is on file,
    which the caller treats as "no evidence" for a job's course requirement.
    """
    education = applicant.educational_background
    if education is None or not education.course_program:
        return ""
    return strip_degree_framing(education.course_program)


def job_course_text(job: Job) -> str:
    """Field-of-study text for the job's preferred course/program (degree
    framing stripped). Empty when the job names no course requirement."""
    if not job.course_program:
        return ""
    return strip_degree_framing(job.course_program)


def applicant_experience_years(applicant: Applicant) -> float:
    """Total years of work experience summed across the applicant's history.

    Ongoing roles (no end date) count up to the current date. Unparseable or
    non-positive spans are ignored. Overlapping roles are not de-duplicated — a
    deliberate approximation for the experience score.
    """
    total_days = 0.0
    now = datetime.now(UTC)
    for experience in applicant.work_experience:
        start = _parse_date(experience.start_date)
        if start is None:
            continue
        end = _parse_date(experience.end_date) or now
        span_days = (end - start).days
        if span_days > 0:
            total_days += span_days
    return total_days / 365.25


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
