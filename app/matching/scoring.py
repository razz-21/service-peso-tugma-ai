"""Rule-based scoring and score combination for the job-matching pipeline.

Pure functions over already-extracted features (no model imports): cosine
similarity for the semantic component, plus skills / experience / education /
location scores, each normalized to [0, 1]. `combined_score` fuses them with the
workspace's configurable weights into the final MatchScore (the paper's Combined
Score Calculation). Component scorers return a reason/matched value alongside the
score for recommendation explainability (`key_matched`).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

# --- Semantic similarity ---------------------------------------------------


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two vectors, clamped to [0, 1].

    Returns 0.0 when either vector has zero magnitude. Negative cosines
    (semantically opposite) are clamped to 0 so the semantic component stays a
    [0, 1] score.
    """
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    similarity = float(np.dot(va, vb)) / denom
    return max(0.0, min(1.0, similarity))


# --- Skills ----------------------------------------------------------------


def skills_match(
    applicant_skills: Sequence[str], required_skills: Sequence[str]
) -> tuple[float, list[str]]:
    """Fraction of the job's required skills the applicant has.

    Case-insensitive exact token match. Returns ``(score, matched)`` where
    ``matched`` lists the satisfied required skills (for explainability). A job
    with no required skills is treated as no constraint (1.0).
    """
    required = [skill.strip() for skill in required_skills if skill.strip()]
    if not required:
        return 1.0, []
    have = {skill.strip().lower() for skill in applicant_skills if skill.strip()}
    matched = [skill for skill in required if skill.lower() in have]
    return len(matched) / len(required), matched


# --- Experience ------------------------------------------------------------

_YEARS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)", flags=re.IGNORECASE)

# Words that carry no field/role signal on their own. Once the "N years" phrase
# is stripped, a requirement made up solely of these is a purely numeric (or
# empty) constraint with nothing qualitative left to semantically match.
_EXPERIENCE_FILLER = frozenset(
    {
        "experience",
        "experiences",
        "work",
        "working",
        "related",
        "relevant",
        "field",
        "minimum",
        "least",
        "at",
        "of",
        "in",
        "the",
        "a",
        "an",
        "and",
        "or",
        "with",
        "plus",
        "preferably",
        "preferred",
        "required",
        "req",
        "years",
        "year",
        "yrs",
        "yr",
        "min",
    }
)

# Empirical cosine band for the MiniLM sentence model: a strongly related
# requirement/experience pair sits around ``_SIM_HIGH``, an unrelated pair around
# ``_SIM_LOW``. Rescaling that band onto [0, 1] lets a genuine field match clear
# the "met" threshold while an unrelated role (e.g. a front-end developer against
# an electrical-engineering requirement) stays well below it. Tunable constants.
_SIM_LOW = 0.15
_SIM_HIGH = 0.55


def parse_required_years(text: str | None) -> float | None:
    """Extract a required-years figure from free text (e.g. ``"3 years"``).

    Returns ``None`` when no ``N year(s)/yr`` pattern is present, which callers
    treat as "no numeric experience constraint".
    """
    if not text:
        return None
    match = _YEARS_RE.search(text)
    return float(match.group(1)) if match is not None else None


def experience_requirement_terms(text: str | None) -> str | None:
    """The qualitative (field/role) part of an experience requirement, if any.

    Strips the ``N years`` phrase and returns what remains only when it carries a
    real field/role signal — e.g. ``"Electrical Engineer"`` from ``"3 years as an
    Electrical Engineer"``. Returns ``None`` for empty text or a purely numeric
    requirement (``"3 years experience"``), which has nothing to match against.
    The returned text is meant to be embedded and compared to the applicant's
    experience; the embedding itself lives outside this pure-scoring module.
    """
    if not text:
        return None
    remainder = _YEARS_RE.sub(" ", text)
    has_signal = any(
        token.lower() not in _EXPERIENCE_FILLER for token in re.findall(r"[a-zA-Z]+", remainder)
    )
    if not has_signal:
        return None
    return re.sub(r"\s+", " ", remainder).strip(" ,.-")


def _calibrate_similarity(cosine: float) -> float:
    """Rescale a raw cosine similarity onto a [0, 1] experience sub-score."""
    return max(0.0, min(1.0, (cosine - _SIM_LOW) / (_SIM_HIGH - _SIM_LOW)))


def experience_match(
    applicant_years: float,
    required_years: float | None,
    qualitative_similarity: float | None = None,
) -> tuple[float, str | None]:
    """Score an applicant's experience against a job's requirement.

    Averages whichever of two independent components are present:

    * **Years** — ratio of ``applicant_years`` to ``required_years`` (capped at
      1.0), when the requirement names a number of years.
    * **Qualitative** — a calibrated semantic similarity between the requirement's
      field/role wording and the applicant's experience, supplied as a raw cosine
      via ``qualitative_similarity`` (the embedding is computed by the caller).

    When neither component is present the requirement is treated as no constraint
    (1.0) — previously the qualitative case was silently treated this way, so a
    field/role requirement always scored "met". Returns ``(score, reason)`` where
    ``reason`` is set only when a years requirement is met.
    """
    years_score: float | None = None
    if required_years is not None and required_years > 0:
        years_score = 0.0 if applicant_years <= 0 else min(applicant_years / required_years, 1.0)

    qualitative_score = (
        _calibrate_similarity(qualitative_similarity)
        if qualitative_similarity is not None
        else None
    )

    components = [score for score in (years_score, qualitative_score) if score is not None]
    if not components:
        return 1.0, None
    score = sum(components) / len(components)
    reason = (
        f"{applicant_years:.1f}+ yrs experience"
        if years_score is not None and years_score >= 1.0
        else None
    )
    return score, reason


# --- Education -------------------------------------------------------------

# Education levels ordered low -> high. Each entry maps keyword fragments to an
# ordinal rank; more specific/higher levels are listed first so the first match
# wins (e.g. "senior high" before "high school", "college graduate" before a
# generic "college").
_EDUCATION_LADDER: tuple[tuple[tuple[str, ...], int], ...] = (
    (("doctor", "phd", "ph.d", "doctorate"), 7),
    (("master", "postgraduate", "post graduate", "graduate studies"), 6),
    (("bachelor", "college graduate", "college degree", "baccalaureate", "tertiary"), 5),
    (
        (
            "associate",
            "vocational",
            "tesda",
            "technical",
            "diploma",
            "college level",
            "college undergraduate",
            "some college",
        ),
        4,
    ),
    (("senior high", "shs", "k-12", "k12"), 3),
    (("high school", "secondary", "junior high"), 2),
    (("elementary", "primary"), 1),
)


def _education_rank(text: str | None) -> int | None:
    if not text:
        return None
    lowered = text.lower()
    for keywords, rank in _EDUCATION_LADDER:
        if any(keyword in lowered for keyword in keywords):
            return rank
    return None


def education_match(
    highest_level: str | None, required_levels: Sequence[str]
) -> tuple[float, str | None]:
    """Whether the applicant's education meets the job's minimum requirement.

    Uses an ordinal education ladder. Returns 1.0 when the applicant meets or
    exceeds the lowest required level, a partial ratio when below, and 1.0 when
    the job states no recognizable requirement. Returns ``(score, reason)``.
    """
    required_ranks = [
        rank for rank in (_education_rank(level) for level in required_levels) if rank is not None
    ]
    if not required_ranks:
        return 1.0, None
    required_rank = min(required_ranks)
    applicant_rank = _education_rank(highest_level)
    if applicant_rank is None:
        return 0.0, None
    if applicant_rank >= required_rank:
        return 1.0, highest_level
    return applicant_rank / required_rank, None


# --- Location --------------------------------------------------------------


def location_match(
    preferred_locations: Sequence[str], job_location: str | None
) -> tuple[float, str | None]:
    """Whether the job's location matches any of the applicant's preferences.

    Substring match in either direction (case-insensitive). A missing job
    location or empty preference list is treated as no constraint (1.0). Returns
    ``(score, matched_location)``.
    """
    if not job_location:
        return 1.0, None
    preferences = [pref.strip() for pref in preferred_locations if pref.strip()]
    if not preferences:
        return 1.0, None
    job_loc = job_location.lower()
    for preference in preferences:
        low = preference.lower()
        if low in job_loc or job_loc in low:
            return 1.0, preference
    return 0.0, None


# --- Combination -----------------------------------------------------------


@dataclass(frozen=True)
class MatchWeights:
    """Weights (fractions summing to ~1.0) for the five scoring components."""

    semantic_similarity: float
    skills: float
    experience: float
    education: float
    location: float

    @classmethod
    def from_percent(
        cls,
        semantic_similarity: float,
        skills: float,
        experience: float,
        education: float,
        location: float,
    ) -> "MatchWeights":
        """Build weights from percentages (e.g. a workspace's MatchingScore)."""
        return cls(
            semantic_similarity=semantic_similarity / 100.0,
            skills=skills / 100.0,
            experience=experience / 100.0,
            education=education / 100.0,
            location=location / 100.0,
        )


# Paper's default weighting profile: 50/20/15/10/5.
DEFAULT_WEIGHTS = MatchWeights.from_percent(50, 20, 15, 10, 5)


@dataclass(frozen=True)
class ScoreBreakdown:
    """The five normalized [0, 1] component scores for one applicant-job pair."""

    semantic_similarity: float
    skills: float
    experience: float
    education: float
    location: float


def combined_score(breakdown: ScoreBreakdown, weights: MatchWeights = DEFAULT_WEIGHTS) -> float:
    """Weighted MatchScore in [0, 1] (the paper's Combined Score Calculation)."""
    return (
        weights.semantic_similarity * breakdown.semantic_similarity
        + weights.skills * breakdown.skills
        + weights.experience * breakdown.experience
        + weights.education * breakdown.education
        + weights.location * breakdown.location
    )
