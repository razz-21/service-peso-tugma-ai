"""Hard primary-requirement gates for job recommendations.

The MatchScore is a *soft* ranking signal, but some job fields are pass/fail:
a job whose primary requirements the applicant categorically fails should never
be recommended, regardless of how well the résumé embeds. These pure predicates
enforce the applicable gates — open vacancies, age range, sex and civil status.

Every gate is "if applicable": it is skipped (treated as a pass) when the job
leaves the field unspecified, or when the applicant lacks the corresponding
datum, so a sparse profile is never penalised by a constraint we cannot check.
"""

import re
from datetime import UTC, date, datetime

# "Female/Male" on either side means "no sex restriction".
_BOTH_SEXES = "female/male"

# 18-30, 18 – 30, 18 to 30
_AGE_RANGE_RE = re.compile(r"(\d{1,3})\s*(?:-|–|—|to)\s*(\d{1,3})")
# Lower-bound phrasings: "18+", "at least 18", "18 and above", "over 18".
_AGE_MIN_RES = (
    re.compile(r"(\d{1,3})\s*(?:\+|and above|and older|or above|or older|and up)"),
    re.compile(r"(?:at least|minimum|min|over|above|older than|from)\D{0,4}(\d{1,3})"),
)
# Upper-bound phrasings: "up to 30", "at most 30", "30 and below", "under 30".
_AGE_MAX_RES = (
    re.compile(r"(\d{1,3})\s*(?:and below|and under|or below|or younger)"),
    re.compile(
        r"(?:up to|at most|maximum|max|under|below|younger than|no more than)\D{0,4}(\d{1,3})"
    ),
)


def applicant_age(date_of_birth: str | None, *, today: date | None = None) -> int | None:
    """Full years old from an ISO-ish birth date, or ``None`` when unparseable."""
    if not date_of_birth:
        return None
    text = date_of_birth.strip()
    birth: datetime | None = None
    try:
        birth = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                birth = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if birth is None:
        return None
    reference = today or datetime.now(UTC).date()
    born = birth.date()
    years = reference.year - born.year - ((reference.month, reference.day) < (born.month, born.day))
    return years if years >= 0 else None


def parse_age_range(text: str | None) -> tuple[int | None, int | None]:
    """Parse a free-text age requirement into ``(min, max)`` bounds.

    Either bound is ``None`` when open-ended or unrecognised; ``(None, None)``
    means no usable constraint (callers treat it as "no restriction").
    """
    if not text:
        return (None, None)
    lowered = text.strip().lower()

    match = _AGE_RANGE_RE.search(lowered)
    if match is not None:
        low, high = int(match.group(1)), int(match.group(2))
        return (min(low, high), max(low, high))

    for pattern in _AGE_MIN_RES:
        match = pattern.search(lowered)
        if match is not None:
            return (int(match.group(1)), None)

    for pattern in _AGE_MAX_RES:
        match = pattern.search(lowered)
        if match is not None:
            return (None, int(match.group(1)))

    return (None, None)


def age_matches(age: int | None, age_range: str | None) -> bool:
    """Whether ``age`` falls within the job's age range (open bounds allowed)."""
    low, high = parse_age_range(age_range)
    if low is None and high is None:
        return True
    if age is None:
        return True
    if low is not None and age < low:
        return False
    return not (high is not None and age > high)


def sex_matches(applicant_sex: str | None, job_sex: str | None) -> bool:
    """Whether the applicant's sex satisfies the job's sex requirement."""
    if job_sex is None:
        return True
    required = str(job_sex).strip().lower()
    if required == _BOTH_SEXES:
        return True
    if applicant_sex is None:
        return True
    provided = str(applicant_sex).strip().lower()
    if provided == _BOTH_SEXES:
        return True
    return provided == required


def civil_status_matches(applicant_civil: str | None, allowed: list[str]) -> bool:
    """Whether the applicant's civil status is among the job's accepted set."""
    if not allowed:
        return True
    if not applicant_civil:
        return True
    target = str(applicant_civil).strip().lower()
    return any(target == str(status).strip().lower() for status in allowed)


def has_open_vacancy(no_of_vacancies: int | None) -> bool:
    """Whether the job still advertises at least one open seat."""
    return no_of_vacancies is None or no_of_vacancies > 0


def meets_primary_requirements(applicant: object, job: object) -> bool:
    """Combined hard primary-requirement gate for recommending ``job`` to ``applicant``.

    Reads attributes defensively so lightweight stand-ins (and models that add
    ``civil_status`` later) work. Returns ``False`` as soon as any applicable
    constraint fails.
    """
    if not has_open_vacancy(getattr(job, "no_of_vacancies", None)):
        return False
    age = applicant_age(getattr(applicant, "date_of_birth", None))
    if not age_matches(age, getattr(job, "age_range", None)):
        return False
    if not sex_matches(getattr(applicant, "sex", None), getattr(job, "sex", None)):
        return False
    return civil_status_matches(
        getattr(applicant, "civil_status", None), getattr(job, "civil_status", None) or []
    )
