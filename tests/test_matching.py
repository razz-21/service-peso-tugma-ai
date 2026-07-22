"""Unit tests for the job-matching AI pipeline (paper Chapter 3).

Covers the pure preprocessing/scoring/profile functions. The embedding model
itself is not loaded here (it downloads ~120 MB and needs no re-testing of the
third-party encoder); only the empty-batch short-circuit and lazy-load guarantee
are checked.
"""

import math
from datetime import date
from types import SimpleNamespace

from app.matching import preprocessing
from app.matching.primary_requirements import (
    age_matches,
    applicant_age,
    civil_status_matches,
    has_open_vacancy,
    meets_primary_requirements,
    parse_age_range,
    sex_matches,
)
from app.matching.profile import (
    applicant_experience_years,
    applicant_to_text,
    job_to_text,
)
from app.matching.scoring import (
    DEFAULT_WEIGHTS,
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

# --- Preprocessing ---------------------------------------------------------


def test_preprocess_cleans_lowercases_and_removes_stopwords() -> None:
    out = preprocessing.preprocess("Senior Python Developer, with 5+ years!! (AWS/Django)")
    # Punctuation stripped, lowercased, stopwords ("with") removed, whitespace collapsed.
    assert out == "senior python developer 5 years aws django"


def test_preprocess_empty_input() -> None:
    assert preprocessing.preprocess("") == ""
    assert preprocessing.preprocess("   ") == ""


# --- Cosine similarity -----------------------------------------------------


def test_cosine_identical_vectors_is_one() -> None:
    assert math.isclose(cosine_similarity([0.1, 0.2, 0.3], [0.1, 0.2, 0.3]), 1.0, abs_tol=1e-9)


def test_cosine_orthogonal_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_opposite_is_clamped_to_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == 0.0


def test_cosine_zero_vector_is_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


# --- Skills ----------------------------------------------------------------


def test_skills_partial_match_and_matched_list() -> None:
    score, matched = skills_match(["Python", "aws"], ["Python", "Django", "AWS"])
    assert math.isclose(score, 2 / 3)
    assert matched == ["Python", "AWS"]  # case-insensitive match, original job casing kept


def test_skills_no_requirement_is_full_score() -> None:
    assert skills_match([], []) == (1.0, [])


def test_skills_applicant_has_none() -> None:
    assert skills_match([], ["Python"]) == (0.0, [])


# --- Experience ------------------------------------------------------------


def test_parse_required_years_variants() -> None:
    assert parse_required_years("Minimum 3 years experience") == 3.0
    assert parse_required_years("2+ yrs") == 2.0
    assert parse_required_years("Fresh graduates welcome") is None
    assert parse_required_years(None) is None


def test_experience_meets_requirement() -> None:
    score, reason = experience_match(5.0, 3.0)
    assert score == 1.0
    assert reason is not None


def test_experience_below_requirement_is_ratio() -> None:
    score, reason = experience_match(1.0, 4.0)
    assert score == 0.25
    assert reason is None


def test_experience_no_requirement_is_full_score() -> None:
    assert experience_match(0.0, None) == (1.0, None)


# --- Education -------------------------------------------------------------


def test_education_meets_or_exceeds() -> None:
    score, reason = education_match("Bachelor's Degree", ["High School"])
    assert score == 1.0
    assert reason == "Bachelor's Degree"


def test_education_below_requirement_is_ratio() -> None:
    score, _ = education_match("High School", ["Bachelor's Degree"])
    assert math.isclose(score, 2 / 5)  # high school rank 2 / bachelor rank 5


def test_education_no_requirement_is_full_score() -> None:
    assert education_match(None, []) == (1.0, None)


def test_education_unknown_applicant_with_requirement() -> None:
    assert education_match(None, ["Bachelor's Degree"]) == (0.0, None)


# --- Location --------------------------------------------------------------


def test_location_matches_preference() -> None:
    score, matched = location_match(["Cagayan de Oro"], "Cagayan de Oro City")
    assert score == 1.0
    assert matched == "Cagayan de Oro"


def test_location_no_match() -> None:
    assert location_match(["Manila"], "Cebu City") == (0.0, None)


def test_location_no_constraint() -> None:
    assert location_match([], "Cebu City") == (1.0, None)
    assert location_match(["Manila"], None) == (1.0, None)


# --- Combination -----------------------------------------------------------


def test_combined_score_matches_paper_worked_example() -> None:
    # Paper §Combined Score Calculation: SS=0.85, SM=0.80, EM=0.70, EDU=0.90,
    # LP=1.00 with default weights (50/20/15/10/5) -> 0.83.
    breakdown = ScoreBreakdown(
        semantic_similarity=0.85, skills=0.80, experience=0.70, education=0.90, location=1.00
    )
    assert math.isclose(combined_score(breakdown, DEFAULT_WEIGHTS), 0.83, abs_tol=1e-9)


def test_default_weights_sum_to_one() -> None:
    weights = DEFAULT_WEIGHTS
    total = (
        weights.semantic_similarity
        + weights.skills
        + weights.experience
        + weights.education
        + weights.location
    )
    assert math.isclose(total, 1.0)


def test_weights_from_percent_normalizes() -> None:
    weights = MatchWeights.from_percent(50, 20, 15, 10, 5)
    assert weights.semantic_similarity == 0.5
    assert weights.location == 0.05


# --- Profile builders ------------------------------------------------------


def _applicant(**overrides: object) -> object:
    """A duck-typed applicant with the fields the profile builders read."""
    base = {
        "technical_skills": ["Python", "FastAPI"],
        "work_experience": [
            SimpleNamespace(position="Backend Developer", company="Acme"),
        ],
        "educational_background": SimpleNamespace(
            highest_education_level="Bachelor's Degree",
            course_program="Computer Science",
            school_university="Liceo",
        ),
        "trainings": [],
        "preferred_occupation_industry": [
            SimpleNamespace(occupation="Software Engineer", industry="IT"),
        ],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_applicant_to_text_includes_skills_and_is_preprocessed() -> None:
    text = applicant_to_text(_applicant())  # type: ignore[arg-type]
    assert "python" in text  # lowercased by preprocessing
    assert "backend developer" in text
    assert "computer science" in text
    assert "," not in text  # punctuation stripped


def test_applicant_to_text_appends_resume_text() -> None:
    text = applicant_to_text(_applicant(), resume_text="Kubernetes Docker")  # type: ignore[arg-type]
    assert "kubernetes" in text and "docker" in text


def test_job_to_text_is_preprocessed() -> None:
    job = SimpleNamespace(
        title="Senior Backend Engineer",
        description="Build APIs.",
        skills_required=["Python", "MongoDB"],
        experience_required="3 years",
        minimum_education_attainment=["Bachelor's Degree"],
    )
    text = job_to_text(job)  # type: ignore[arg-type]
    assert "senior backend engineer" in text
    assert "mongodb" in text
    assert "." not in text


def test_applicant_experience_years_sums_closed_spans() -> None:
    applicant = SimpleNamespace(
        work_experience=[
            SimpleNamespace(start_date="2018-01-01", end_date="2020-01-01"),  # ~2 yrs
            SimpleNamespace(start_date="2020-01-01", end_date="2023-01-01"),  # ~3 yrs
        ]
    )
    years = applicant_experience_years(applicant)  # type: ignore[arg-type]
    assert math.isclose(years, 5.0, abs_tol=0.05)


def test_applicant_experience_years_ignores_unparseable_dates() -> None:
    applicant = SimpleNamespace(
        work_experience=[
            SimpleNamespace(start_date=None, end_date=None),
            SimpleNamespace(start_date="not-a-date", end_date="2020-01-01"),
        ]
    )
    assert applicant_experience_years(applicant) == 0.0  # type: ignore[arg-type]


# --- Embeddings (no model load) --------------------------------------------


def test_embed_batch_empty_short_circuits_without_loading_model() -> None:
    from app.matching import embeddings

    assert embeddings.embed_batch([]) == []
    assert embeddings._model is None  # still lazy — empty batch never loads the model


# --- Primary requirements --------------------------------------------------

_TODAY = date(2026, 7, 22)


def test_applicant_age_from_iso_date() -> None:
    assert applicant_age("1996-05-20", today=_TODAY) == 30
    # Birthday not yet reached this year.
    assert applicant_age("1996-12-01", today=_TODAY) == 29


def test_applicant_age_unparseable_is_none() -> None:
    assert applicant_age(None) is None
    assert applicant_age("not-a-date") is None


def test_parse_age_range_variants() -> None:
    assert parse_age_range("18-30") == (18, 30)
    assert parse_age_range("18 to 30") == (18, 30)
    assert parse_age_range("21+") == (21, None)
    assert parse_age_range("at least 21") == (21, None)
    assert parse_age_range("up to 40") == (None, 40)
    assert parse_age_range("30 and below") == (None, 30)
    assert parse_age_range(None) == (None, None)
    assert parse_age_range("any age") == (None, None)


def test_age_matches_within_and_outside_range() -> None:
    assert age_matches(25, "18-30") is True
    assert age_matches(31, "18-30") is False
    assert age_matches(17, "18-30") is False
    # No constraint / unknown age → always passes.
    assert age_matches(15, None) is True
    assert age_matches(None, "18-30") is True


def test_sex_matches_rules() -> None:
    assert sex_matches("Female", "Female") is True
    assert sex_matches("Male", "Female") is False
    # "Female/Male" on the job means no restriction.
    assert sex_matches("Male", "Female/Male") is True
    # No job requirement, or unknown applicant sex → passes.
    assert sex_matches("Male", None) is True
    assert sex_matches(None, "Female") is True


def test_civil_status_matches_rules() -> None:
    assert civil_status_matches("Single", ["Single", "Married"]) is True
    assert civil_status_matches("Widowed", ["Single", "Married"]) is False
    assert civil_status_matches("single", ["Single"]) is True  # case-insensitive
    # No requirement, or unknown applicant status → passes.
    assert civil_status_matches("Single", []) is True
    assert civil_status_matches(None, ["Single"]) is True


def test_has_open_vacancy() -> None:
    assert has_open_vacancy(2) is True
    assert has_open_vacancy(0) is False
    assert has_open_vacancy(None) is True


def test_meets_primary_requirements_all_gates_pass() -> None:
    applicant = SimpleNamespace(date_of_birth="1996-05-20", sex="Male")
    job = SimpleNamespace(no_of_vacancies=2, age_range="18-40", sex="Female/Male", civil_status=[])
    assert meets_primary_requirements(applicant, job) is True


def test_meets_primary_requirements_fails_on_each_gate() -> None:
    applicant = SimpleNamespace(date_of_birth="1996-05-20", sex="Male")  # age 30 as of _TODAY
    base = dict(no_of_vacancies=1, age_range="18-40", sex="Male", civil_status=[])

    assert (
        meets_primary_requirements(applicant, SimpleNamespace(**{**base, "no_of_vacancies": 0}))
        is False
    )
    assert (
        meets_primary_requirements(applicant, SimpleNamespace(**{**base, "age_range": "18-25"}))
        is False
    )
    assert (
        meets_primary_requirements(applicant, SimpleNamespace(**{**base, "sex": "Female"})) is False
    )


def test_meets_primary_requirements_skips_unavailable_data() -> None:
    # Sparse applicant + unconstrained job → passes (every gate not applicable).
    applicant = SimpleNamespace(date_of_birth=None, sex=None)
    job = SimpleNamespace(no_of_vacancies=1, age_range=None, sex=None, civil_status=[])
    assert meets_primary_requirements(applicant, job) is True
