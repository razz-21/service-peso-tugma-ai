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
    if job.experience_required:
        parts.append(job.experience_required)
    if job.minimum_education_attainment:
        parts.append(" ".join(job.minimum_education_attainment))
    return preprocess(_join(parts))


def applicant_experience_text(applicant: Applicant) -> str:
    """Preprocessed text of the applicant's experience and qualifications.

    Concatenates work positions/companies and educational level/course — the
    fields a free-text experience requirement (a role or a field of study) is
    matched against semantically. The education portion has its degree framing
    stripped (so a field-of-study requirement compares "Information Technology"
    vs "Logistics", not the shared "Bachelor of Science / Degree" scaffolding),
    while work text is left intact so role titles like "Field Engineer" survive.
    Empty when the applicant has neither, which the caller treats as "no
    evidence" for a qualitative requirement.
    """
    work_parts = [
        _join([experience.position or "", experience.company or ""])
        for experience in applicant.work_experience
    ]
    work_text = preprocess(_join(work_parts))
    education = applicant.educational_background
    education_text = (
        strip_degree_framing(
            _join([education.highest_education_level or "", education.course_program or ""])
        )
        if education is not None
        else ""
    )
    return " ".join(text for text in (work_text, education_text) if text)


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
