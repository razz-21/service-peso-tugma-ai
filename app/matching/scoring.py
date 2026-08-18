"""Rule-based scoring and score combination for the job-matching pipeline.

Pure functions over already-extracted features (no model imports): cosine
similarity for the semantic component, plus skills / experience / education /
location scores, each normalized to [0, 1]. `combined_score` fuses them with the
workspace's configurable weights into the final MatchScore (the paper's Combined
Score Calculation). Component scorers return a reason/matched value alongside the
score for recommendation explainability (`key_matched`).
"""

import re
from collections.abc import Mapping, Sequence
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


# --- Requirement tiering ---------------------------------------------------

# Default cap on how much a fully-satisfied *preferred* tier can lift a criterion
# whose *mandatory* tier is met (0.80 → 1.00). Mirrors the config tunable
# ``REQUIREMENT_BONUS_CAP``; kept as a module default so this stays model/config-
# free and pure. Callers (the recommender) may pass the workspace/config value.
_DEFAULT_BONUS_CAP = 0.20


def tiered_score(
    cov_mandatory: float,
    cov_preferred: float,
    *,
    has_mandatory: bool,
    has_preferred: bool = True,
    bonus_cap: float = _DEFAULT_BONUS_CAP,
) -> float:
    """Combine mandatory coverage (must-have) with a capped preferred bonus.

    ``cov_mandatory`` / ``cov_preferred`` are the [0, 1] coverage of a criterion's
    mandatory and preferred item sets.

    Tiering is *opt-in per criterion*: when there is no preferred tier
    (``has_preferred`` is False) it is inert and the criterion keeps its raw
    mandatory coverage, so an existing all-mandatory job scores and ranks exactly
    as before. When a preferred tier exists the cap reserves ``bonus_cap`` of
    headroom: meeting every mandatory item lands at ``1 - bonus_cap`` and the
    preferred coverage fills the rest (up to 1.0); a missing mandatory item is
    bounded below ``1 - bonus_cap`` and the preferred tier can never compensate.
    When there is no mandatory constraint (``has_mandatory`` is False) the base is
    full and only the preferred bonus applies. The embedding/cosine layer is
    untouched throughout.
    """
    cap = bonus_cap if has_preferred else 0.0
    if not has_mandatory:  # no mandatory constraint → base is full
        cov_mandatory = 1.0
    if cov_mandatory >= 1.0:
        return (1 - cap) + cap * cov_preferred
    return (1 - cap) * cov_mandatory


# --- Skills ----------------------------------------------------------------

# Empirical cosine band for skill-to-skill MiniLM similarity (mirrors the
# experience band). Identical or synonymous skills ("JavaScript" / "JS",
# "React.js" / "React") sit near ``_SKILL_SIM_HIGH``; unrelated skills near
# ``_SKILL_SIM_LOW``. Rescaling onto [0, 1] gives partial credit for a related
# (not identical) applicant skill. A required skill counts as "covered" for
# explainability once its best match clears ``_SKILL_MATCH_THRESHOLD``. Tunable.
_SKILL_SIM_LOW = 0.35
_SKILL_SIM_HIGH = 0.75
_SKILL_MATCH_THRESHOLD = 0.62
# Below the full-match bar but still clearly related: a nearby tool/skill (e.g.
# "Google Sheets" for a required "Excel") that should surface as a distinct
# "related" hint in the compare modal rather than a hard miss. Tunable.
_SKILL_RELATED_THRESHOLD = 0.50


def _calibrate_skill_similarity(cosine: float) -> float:
    """Rescale a raw skill-to-skill cosine onto a [0, 1] coverage sub-score."""
    return max(0.0, min(1.0, (cosine - _SKILL_SIM_LOW) / (_SKILL_SIM_HIGH - _SKILL_SIM_LOW)))


def _skill_coverage(
    applicant_skills: Sequence[str],
    skills: Sequence[str],
    similarities: Mapping[str, float] | None,
) -> tuple[float, list[str]]:
    """Coverage of one tier of skills by the applicant, plus the matched ones.

    Each skill contributes a [0, 1] sub-score — 1.0 for a case-insensitive exact
    token match, else the calibrated MiniLM similarity when ``similarities`` is
    supplied (0.0 otherwise) — and the returned coverage averages them. An empty
    tier has no items to cover and returns ``(0.0, [])``; callers decide what an
    empty tier means (see :func:`skills_match`).
    """
    items = [skill.strip() for skill in skills if skill.strip()]
    if not items:
        return 0.0, []
    have = {skill.strip().lower() for skill in applicant_skills if skill.strip()}
    sub_scores: list[float] = []
    matched: list[str] = []
    for skill in items:
        if skill.lower() in have:
            sub_scores.append(1.0)
            matched.append(skill)
        elif similarities is not None:
            cosine = similarities.get(skill, 0.0)
            sub_scores.append(_calibrate_skill_similarity(cosine))
            if cosine >= _SKILL_MATCH_THRESHOLD:
                matched.append(skill)
        else:
            sub_scores.append(0.0)
    return sum(sub_scores) / len(items), matched


def skills_match(
    applicant_skills: Sequence[str],
    required_skills: Sequence[str],
    similarities: Mapping[str, float] | None = None,
    preferred_skills: Sequence[str] = (),
    *,
    bonus_cap: float = _DEFAULT_BONUS_CAP,
) -> tuple[float, list[str]]:
    """Tiered skills score: mandatory coverage plus a capped preferred bonus.

    ``required_skills`` is the mandatory (must-have) tier and ``preferred_skills``
    the preferred (nice-to-have) tier. Each tier's coverage is computed with the
    *same* exact + semantic matching (see :func:`_skill_coverage`): a skill is
    satisfied by a case-insensitive exact token match or — when ``similarities``
    is supplied — by a semantically related applicant skill ("JS" for
    "JavaScript"). ``similarities`` maps each skill (stripped, either tier) to the
    best MiniLM cosine against any applicant skill, computed by the caller so this
    module stays model-free. The two coverages combine via :func:`tiered_score`,
    so when preferred skills are set a missing mandatory skill caps the score below
    ``1 - bonus_cap`` while preferred skills only add a bounded bonus; with no
    preferred tier the score is the plain mandatory coverage (unchanged behavior).
    Returns ``(score, matched)`` where ``matched`` lists satisfied skills from
    *both* tiers for explainability. A job with no skills in either tier is treated
    as no constraint (1.0).
    """
    mandatory = [skill.strip() for skill in required_skills if skill.strip()]
    preferred = [skill.strip() for skill in preferred_skills if skill.strip()]
    if not mandatory and not preferred:
        return 1.0, []
    cov_m, matched_m = _skill_coverage(applicant_skills, mandatory, similarities)
    cov_o, matched_o = _skill_coverage(applicant_skills, preferred, similarities)
    score = tiered_score(
        cov_m,
        cov_o,
        has_mandatory=bool(mandatory),
        has_preferred=bool(preferred),
        bonus_cap=bonus_cap,
    )
    return score, matched_m + matched_o


# One required skill's coverage by the applicant, for the compare modal. ``state``
# is ``"matched"`` (exact token or a strong semantic match), ``"related"`` (a
# nearby skill worth surfacing but short of a full match), or ``"missing"``.
# ``applicant`` names the skill that best covers it — ``None`` for an exact token
# match (same text as ``required``) or a genuine miss.
@dataclass(frozen=True)
class SkillMatch:
    required: str
    applicant: str | None
    similarity: float  # best raw cosine (0-1); 1.0 for an exact token match
    state: str  # "matched" | "related" | "missing"
    tier: str  # "mandatory" | "preferred"


def _classify_one(
    skill: str,
    have: set[str],
    sources: Mapping[str, tuple[str | None, float]] | None,
    tier: str,
) -> SkillMatch:
    """Classify a single skill into matched / related / missing for the modal."""
    if skill.lower() in have:
        return SkillMatch(skill, None, 1.0, "matched", tier)
    applicant, cosine = sources.get(skill, (None, 0.0)) if sources else (None, 0.0)
    if cosine >= _SKILL_MATCH_THRESHOLD:
        return SkillMatch(skill, applicant, cosine, "matched", tier)
    if cosine >= _SKILL_RELATED_THRESHOLD:
        return SkillMatch(skill, applicant, cosine, "related", tier)
    # Too weak to credit or name a source — a genuine gap.
    return SkillMatch(skill, None, cosine, "missing", tier)


def classify_skill_matches(
    applicant_skills: Sequence[str],
    required_skills: Sequence[str],
    sources: Mapping[str, tuple[str | None, float]] | None = None,
    preferred_skills: Sequence[str] = (),
) -> list[SkillMatch]:
    """Per-skill coverage detail (both tiers) for the applicant-vs-job compare modal.

    Mirrors :func:`skills_match`'s matching rules but keeps *which* applicant
    skill covers each requirement (and how strongly), so the UI can show
    "Excel · via Google Sheets" and split matched/related/missing — and now which
    ``tier`` each requirement belongs to, so a gap can read "missing (required)"
    vs. "missing (preferred)". ``sources`` maps each skill (stripped, either tier)
    to ``(best_applicant_skill, best_cosine)`` — the argmax the caller already
    computes for the semantic skills score. Exact token matches are credited
    without needing ``sources``. Mandatory skills are listed first.
    """
    have = {skill.strip().lower() for skill in applicant_skills if skill.strip()}
    mandatory = [skill.strip() for skill in required_skills if skill.strip()]
    preferred = [skill.strip() for skill in preferred_skills if skill.strip()]
    matches = [_classify_one(skill, have, sources, "mandatory") for skill in mandatory]
    matches += [_classify_one(skill, have, sources, "preferred") for skill in preferred]
    return matches


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

# The experience sub-score at/above which the requirement reads as "Met". Kept in
# sync with the frontend's `statusFromScore` cutoff (score >= 80 -> met) so the
# backend only surfaces a "N+ yrs experience" chip when the gate as a whole — the
# field/role *and* the years — would actually show "Met". Mirrors the education
# match's `_EDUCATION_MET_THRESHOLD`.
_EXPERIENCE_MET_THRESHOLD = 0.8


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


def _experience_tier_coverage(
    applicant_years: float,
    required_years: float | None,
    qualitative_similarity: float | None,
) -> tuple[float, float | None] | None:
    """Coverage of a single experience tier (required *or* preferred).

    A tier may carry two independent constraints, and the applicant must satisfy
    **both** — so they combine as a gate (the minimum), not an average:

    * **Years** — ratio of ``applicant_years`` to ``required_years`` (capped at
      1.0), when the tier names a number of years.
    * **Qualitative** — a calibrated semantic similarity between the tier's
      field/role wording and the applicant's experience, supplied as a raw cosine
      via ``qualitative_similarity`` (the embedding is computed by the caller).

    Taking the minimum means met years can no longer mask an unrelated field
    (e.g. a Quality Assurance / Developer background against a "5 years as a pet
    salon staff" requirement): the near-zero qualitative score becomes the tier
    score, so it reads as unmet rather than "partial". Mirrors the education
    match's level/field gate.

    Returns ``(coverage, years_score)`` where ``years_score`` is ``None`` when the
    tier names no number of years (used by the caller only to decide the "N+ yrs"
    chip). Returns ``None`` when the tier states neither constraint (no evidence
    to score against).
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
        return None
    # Gate, not average: the weakest satisfied constraint bounds the score, so an
    # unrelated field can't be lifted into "partial"/"met" by met years.
    return min(components), years_score


def experience_match(
    applicant_years: float,
    required_years: float | None,
    required_similarity: float | None = None,
    preferred_years: float | None = None,
    preferred_similarity: float | None = None,
    *,
    bonus_cap: float = _DEFAULT_BONUS_CAP,
) -> tuple[float, str | None]:
    """Score an applicant's experience against a job's two-tier requirement.

    The job may state a **mandatory** experience (``required_years`` /
    ``required_similarity``) and a **preferred** one (``preferred_years`` /
    ``preferred_similarity``); each tier gates its own years-and-field
    constraints via :func:`_experience_tier_coverage`. The two tiers then combine
    exactly like skills/education: the mandatory gate is ``cov_mandatory`` and the
    preferred gate feeds a capped bonus through :func:`tiered_score`.

    When the job states neither tier the requirement is treated as no constraint
    (1.0). With only a mandatory tier the score is the plain gate (unchanged
    behavior); with a preferred tier present, a met mandatory tier sits in
    ``[1 - bonus_cap, 1.0]`` and a met preferred tier lifts it toward 1.0 — an
    unmet preferred tier adds no penalty.

    Returns ``(score, reason)`` where ``reason`` — the applicant's years — is set
    only when the **mandatory** gate clears the "Met" threshold, so neither an
    unrelated field nor a preferred boost ever surfaces a "N+ yrs experience" chip.
    """
    mandatory = _experience_tier_coverage(applicant_years, required_years, required_similarity)
    preferred = _experience_tier_coverage(applicant_years, preferred_years, preferred_similarity)

    if mandatory is None and preferred is None:
        return 1.0, None

    cov_mandatory, mandatory_years_score = mandatory if mandatory is not None else (0.0, None)
    cov_preferred = preferred[0] if preferred is not None else 0.0
    score = tiered_score(
        cov_mandatory,
        cov_preferred,
        has_mandatory=mandatory is not None,
        has_preferred=preferred is not None,
        bonus_cap=bonus_cap,
    )
    # The "Met" chip keys off the mandatory gate alone, so a preferred boost never
    # fabricates a "Met" chip and an unrelated mandatory field never surfaces one.
    reason = (
        f"{applicant_years:.1f}+ yrs experience"
        if mandatory_years_score is not None
        and mandatory_years_score >= 1.0
        and cov_mandatory >= _EXPERIENCE_MET_THRESHOLD
        else None
    )
    return score, reason


# --- Education -------------------------------------------------------------

# The education sub-score at/above which the requirement reads as "Met". Kept in
# sync with the frontend's `statusFromScore` cutoff (score >= 80 -> met) so the
# backend only credits a matched degree in the explainability chips when the UI
# would actually show "Met". Under the min-gate this also fixes the effective
# field-of-study bar: education is "Met" only when the calibrated course
# similarity itself clears 0.8, i.e. a genuinely related field.
_EDUCATION_MET_THRESHOLD = 0.8

# Sub-score lost per ladder rung the applicant falls short of the required level.
# The ladder is *ordinal*, so distance — not the raw rank quotient — is what
# carries meaning: one rung short anchors at 0.5 ("Partial") and each further
# rung subtracts a step, so two rungs short already reads "Not met".
_EDUCATION_LEVEL_STEP = 0.25
_EDUCATION_ONE_RUNG_SHORT = 0.5

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


def _level_ordinal_score(applicant_rank: int | None, required_rank: int) -> float:
    """Ordinal [0, 1] score for meeting one education level.

    1.0 when the applicant meets/exceeds ``required_rank``; below it the score
    decays by *distance* (one rung short → 0.5, each further rung −0.25); 0.0 when
    the applicant's level is unrecognized. Shared by the mandatory-level gate and
    the preferred-level coverage so both read the ladder identically.
    """
    if applicant_rank is None:
        return 0.0
    if applicant_rank >= required_rank:
        return 1.0
    gap = required_rank - applicant_rank
    return max(0.0, _EDUCATION_ONE_RUNG_SHORT - (gap - 1) * _EDUCATION_LEVEL_STEP)


def education_match(
    highest_level: str | None,
    required_levels: Sequence[str],
    course_similarity: float | None = None,
) -> tuple[float, str | None]:
    """Whether the applicant's education meets the job's requirement.

    The job may specify two independent constraints, and the applicant must
    satisfy **both** — so they combine as a gate (the minimum), not an average:

    * **Level** — an ordinal comparison of the applicant's highest education
      level against the job's minimum attainment ladder: 1.0 when the applicant
      meets/exceeds the lowest required level; below it the score decays by
      *distance* (one rung short → 0.5, each further rung −0.25) rather than the
      raw rank quotient, which overstated "one rung below" (rank 4 / rank 5 =
      0.8 read a vocational grad as 80% of a bachelor's); 0.0 when the
      applicant's level is unrecognized.
    * **Course/program** — a calibrated semantic similarity between the job's
      preferred course of study and the applicant's, supplied as a raw MiniLM
      cosine via ``course_similarity`` (the embedding is computed by the
      caller, mirroring the qualitative experience match).

    Taking the minimum means a met level can no longer mask an unrelated field
    (e.g. a Business Administration graduate against a Biology/Chemistry
    requirement): the low course score becomes the education score, so the
    requirement reads as unmet rather than "Met". A field is only credited when
    its calibrated similarity clears the gate the caller uses for "Met".

    When the job states neither a recognizable level nor a course the
    requirement is treated as no constraint (1.0). Returns ``(score, reason)``
    with ``reason`` set to the applicant's level only when the education gate as
    a whole passes — otherwise the reason would surface a matched degree in the
    explainability chips for an applicant whose field does not fit.

    Education has no preferred tier (unlike skills/experience): the minimum
    attainment already captures the level requirement, so the score is the plain
    mandatory level/course gate.
    """
    # Ordinal minimum-education-level component (the mandatory attainment).
    required_ranks = [
        rank for rank in (_education_rank(level) for level in required_levels) if rank is not None
    ]
    level_score: float | None
    level_reason: str | None = None
    if not required_ranks:
        level_score = None
    else:
        # Ordinal distance decay, not the rank quotient: one rung short reads
        # "Partial", two rungs short "Not met". Ranks aren't a ratio scale.
        required_rank = min(required_ranks)
        applicant_rank = _education_rank(highest_level)
        level_score = _level_ordinal_score(applicant_rank, required_rank)
        if level_score >= 1.0:
            level_reason = highest_level

    # Course/program field-of-study component. Reuses the experience match's
    # MiniLM calibration band since both compare the same sentence model's
    # cosines onto a [0, 1] sub-score.
    course_score = (
        _calibrate_similarity(course_similarity) if course_similarity is not None else None
    )

    # Coverage: gate (min), not average — the weakest satisfied constraint bounds
    # it, so an unrelated field can't be lifted into "Met" by a met level (and vice
    # versa). No recognizable level and no course requirement → no constraint (1.0).
    components = [score for score in (level_score, course_score) if score is not None]
    if not components:
        return 1.0, None

    score = min(components)
    reason = level_reason if score >= _EDUCATION_MET_THRESHOLD else None
    return score, reason


# --- Mandatory gate (optional hard-knockout mode) --------------------------

# These mirror each component's "mandatory tier fully satisfied" test, used only
# when the workspace/config enables ``GATE_ON_MANDATORY`` to exclude a job whose
# applicant fails any must-have (see recommended_jobs_service). They evaluate the
# mandatory tier alone (no preferred bonus). A criterion the job doesn't state is
# vacuously satisfied, matching the "no constraint" behavior of the scorers.


def skills_mandatory_covered(
    applicant_skills: Sequence[str],
    required_skills: Sequence[str],
    similarities: Mapping[str, float] | None = None,
) -> bool:
    """Whether every *mandatory* required skill is covered (exact or strong semantic).

    Uses the same coverage rule as :func:`skills_match` (exact token match, or a
    MiniLM cosine clearing ``_SKILL_MATCH_THRESHOLD``). A job with no required
    skills is vacuously covered.
    """
    required = [skill.strip() for skill in required_skills if skill.strip()]
    if not required:
        return True
    _, matched = _skill_coverage(applicant_skills, required, similarities)
    return len(matched) == len(required)


def education_mandatory_met(
    highest_level: str | None,
    required_levels: Sequence[str],
    course_similarity: float | None = None,
) -> bool:
    """Whether the applicant meets the *mandatory* education tier (no preferred bonus)."""
    score, _ = education_match(highest_level, required_levels, course_similarity)
    return score >= _EDUCATION_MET_THRESHOLD


def experience_mandatory_met(
    applicant_years: float,
    required_years: float | None,
    required_similarity: float | None = None,
) -> bool:
    """Whether the applicant meets a job's *mandatory* experience tier (no preferred bonus)."""
    score, _ = experience_match(applicant_years, required_years, required_similarity)
    return score >= _EXPERIENCE_MET_THRESHOLD


# --- Location --------------------------------------------------------------


# Administrative-level noise words stripped so "Cagayan de Oro City" and
# "Cagayan de Oro" compare equal ("City of X" / "X City", "X Province", ...).
_LOCATION_NOISE = re.compile(r"^(city of |municipality of |province of )|( city| province)$")

# Graded location credit: a shared city/municipality (or barangay within a
# consistent province) is a full match; sharing only the province is partial.
_LOCATION_CITY_SCORE = 1.0
_LOCATION_PROVINCE_SCORE = 0.6


def _location_parts(value: str) -> list[str]:
    """Normalized location components in order (most specific → province last).

    Splits on commas, lowercases, trims, and strips administrative-level noise
    words so hierarchy parts (barangay / city / province) compare directly
    regardless of the surrounding qualifier text. Order and duplicates are
    preserved so the province (last element) can be identified positionally.
    """
    parts: list[str] = []
    for part in value.split(","):
        token = _LOCATION_NOISE.sub("", part.strip().lower()).strip()
        if token:
            parts.append(token)
    return parts


def _phrase_in(parts: list[str], phrase: str) -> bool:
    """Whether ``phrase`` occurs as a contiguous whole-word run across ``parts``.

    Tolerates one side being free text without comma separators — e.g. the
    job part "cagayan de oro" is found inside a preference typed as
    "kauswagan cagayan de oro". Word-boundary framing keeps it safe (" oro "
    is not matched inside "toronto").
    """
    haystack = f" {' '.join(parts)} "
    return f" {phrase} " in haystack


def _location_score(pref_parts: list[str], job_parts: list[str]) -> float:
    """Graded overlap between two normalized, ordered locations.

    * ``1.0`` — they agree at city/municipality (or finer) level: a shared
      component that is not merely the province, with provinces that don't
      conflict. Guards against coincidental barangay-name collisions (e.g.
      "Poblacion") between two *different* provinces.
    * ``0.6`` — same province only (both name a province and they're equal, but
      nothing more specific lines up).
    * ``0.0`` — no shared component.

    A component counts as shared when it appears (as a whole-word run) on the
    other side, not only on an exact part-for-part equality, so a location typed
    as free text ("Kauswagan Cagayan de Oro") still matches a comma-qualified one
    ("Kauswagan, Cagayan de Oro City, Misamis Oriental").
    """
    if not pref_parts or not job_parts:
        return 0.0
    shared = {part for part in job_parts if _phrase_in(pref_parts, part)}
    shared |= {part for part in pref_parts if _phrase_in(job_parts, part)}
    if not shared:
        return 0.0
    # Province = trailing component, but only when a location has ≥2 parts; a
    # bare single token (e.g. "Cagayan de Oro") is treated as a city, not a
    # province, so it still fully matches a qualified "…, Cagayan de Oro, …".
    pref_prov = pref_parts[-1] if len(pref_parts) >= 2 else None
    job_prov = job_parts[-1] if len(job_parts) >= 2 else None
    provinces_conflict = pref_prov is not None and job_prov is not None and pref_prov != job_prov
    specific_shared = shared - {pref_prov, job_prov}
    if specific_shared and not provinces_conflict:
        return _LOCATION_CITY_SCORE
    if pref_prov is not None and pref_prov == job_prov:
        return _LOCATION_PROVINCE_SCORE
    return 0.0


def location_match(
    preferred_locations: Sequence[str], job_location: str | None
) -> tuple[float, str | None]:
    """Graded match of the job's location against the applicant's preferences.

    Compares normalized location components hierarchically and returns the
    *best-scoring* preference: ``1.0`` for a shared city/municipality (so
    "Cagayan de Oro City, Misamis Oriental" fully matches "Bulua, Cagayan de
    Oro, Misamis Oriental"), ``0.6`` for a shared province only (a soft partial
    credit), and ``0.0`` for no overlap. A missing job location or empty
    preference list is treated as no constraint (1.0). Returns
    ``(score, matched_location)`` with ``matched_location`` set to the
    best-matching preference (``None`` when nothing matches).
    """
    if not job_location:
        return 1.0, None
    preferences = [pref.strip() for pref in preferred_locations if pref.strip()]
    if not preferences:
        return 1.0, None
    job_parts = _location_parts(job_location)
    if not job_parts:
        return 1.0, None
    best_score = 0.0
    best_pref: str | None = None
    for preference in preferences:
        score = _location_score(_location_parts(preference), job_parts)
        if score > best_score:
            best_score, best_pref = score, preference
    if best_pref is None:
        return 0.0, None
    return best_score, best_pref


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
