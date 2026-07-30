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
    applicant_course_text,
    applicant_experience_text,
    applicant_experience_years,
    applicant_to_text,
    job_course_text,
    job_to_text,
)
from app.matching.scoring import (
    DEFAULT_WEIGHTS,
    MatchWeights,
    ScoreBreakdown,
    classify_skill_matches,
    combined_score,
    cosine_similarity,
    education_match,
    experience_match,
    experience_requirement_terms,
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


def test_strip_degree_framing_removes_scaffolding_keeps_field() -> None:
    # Degree phrases lose their "Bachelor of ... Degree in ... related field"
    # scaffolding so only the field of study remains for comparison.
    requirement = (
        "Degree in Logistics, Supply Chain Management, Business, Administration or related field"
    )
    assert (
        preprocessing.strip_degree_framing(requirement)
        == "logistics supply chain management business administration"
    )
    assert (
        preprocessing.strip_degree_framing("Bachelor of Science in Information Technology")
        == "science information technology"
    )


def test_strip_degree_framing_leaves_non_degree_text_intact() -> None:
    # A plain role carries no degree indicator, so nothing is stripped — "field"
    # in a job title survives.
    assert preprocessing.strip_degree_framing("Field Engineer") == "field engineer"
    assert preprocessing.strip_degree_framing("Electrical Engineer") == "electrical engineer"


def test_strip_degree_framing_bare_level_collapses_to_empty() -> None:
    # A degree *level* with no field of study strips to nothing (left to the
    # education ladder, not the qualitative experience match).
    assert preprocessing.strip_degree_framing("Bachelor's Degree") == ""
    assert preprocessing.strip_degree_framing("") == ""


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


def test_skills_semantic_credits_related_skill() -> None:
    # "JS" is not an exact match for "JavaScript", but a high MiniLM cosine
    # credits it as covered.
    score, matched = skills_match(["JS"], ["JavaScript"], {"JavaScript": 0.8})
    assert matched == ["JavaScript"]
    assert score == 1.0  # calibrated 0.8 clamps to 1.0 (>= high band)


def test_skills_semantic_below_threshold_not_matched() -> None:
    score, matched = skills_match(["Nursing"], ["JavaScript"], {"JavaScript": 0.2})
    assert matched == []
    assert score == 0.0  # calibrated below the low band floors to 0


def test_skills_semantic_partial_credit_below_match_threshold() -> None:
    # cosine 0.55 -> calibrated (0.55-0.35)/(0.75-0.35) = 0.5, but below the 0.62
    # match threshold, so it lends partial score without being listed as matched.
    score, matched = skills_match(["Java"], ["Kotlin"], {"Kotlin": 0.55})
    assert matched == []
    assert math.isclose(score, 0.5)


def test_skills_exact_match_takes_precedence_over_low_similarity() -> None:
    score, matched = skills_match(["Python"], ["Python"], {"Python": 0.0})
    assert matched == ["Python"]
    assert score == 1.0


def test_skills_semantic_mix_exact_and_related() -> None:
    # One exact match (1.0) and one semantic match (0.7 -> calibrated ~0.875).
    score, matched = skills_match(["Python", "React"], ["Python", "React.js"], {"React.js": 0.7})
    assert matched == ["Python", "React.js"]
    assert math.isclose(score, (1.0 + (0.7 - 0.35) / (0.75 - 0.35)) / 2)


# --- Skill match detail (compare modal) ------------------------------------


def test_classify_skill_matches_exact_token() -> None:
    [match] = classify_skill_matches(["Excel"], ["Excel"])
    assert match.state == "matched"
    assert match.applicant is None  # exact: same text, no "via"
    assert match.similarity == 1.0


def test_classify_skill_matches_related_surfaces_source() -> None:
    # Google Sheets ~ Excel: below the full-match bar (0.62) but above related (0.50).
    [match] = classify_skill_matches(
        ["Google Sheets"], ["Excel"], {"Excel": ("Google Sheets", 0.55)}
    )
    assert match.state == "related"
    assert match.applicant == "Google Sheets"
    assert math.isclose(match.similarity, 0.55)


def test_classify_skill_matches_strong_semantic_is_matched() -> None:
    [match] = classify_skill_matches(["JS"], ["JavaScript"], {"JavaScript": ("JS", 0.7)})
    assert match.state == "matched"
    assert match.applicant == "JS"


def test_classify_skill_matches_weak_is_missing_without_source() -> None:
    # An unrelated best skill stays below the related bar and is not named.
    [match] = classify_skill_matches(["Canva"], ["Excel"], {"Excel": ("Canva", 0.2)})
    assert match.state == "missing"
    assert match.applicant is None


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


def test_experience_requirement_terms_extracts_qualitative_signal() -> None:
    # Purely numeric / filler requirements have nothing qualitative to match.
    assert experience_requirement_terms("3 years experience") is None
    assert experience_requirement_terms("Minimum 2 yrs of related work") is None
    assert experience_requirement_terms(None) is None
    assert experience_requirement_terms("") is None
    # Field/role wording survives, with any years phrase stripped out.
    terms = experience_requirement_terms("2+ years as an Electrical Engineer")
    assert terms is not None and "Electrical Engineer" in terms
    assert "years" not in terms
    assert (
        experience_requirement_terms(
            "Engineering Graduate preferably Electrical Engineering, Mechanical Engineering"
        )
        == "Engineering Graduate preferably Electrical Engineering, Mechanical Engineering"
    )


def test_experience_qualitative_low_similarity_is_not_met() -> None:
    # A front-end developer against an engineering requirement: low cosine ->
    # near-zero sub-score (the reported bug — previously always 1.0 / "met").
    score, reason = experience_match(3.0, None, qualitative_similarity=0.15)
    assert score == 0.0
    assert reason is None


def test_experience_qualitative_high_similarity_is_met() -> None:
    score, reason = experience_match(0.0, None, qualitative_similarity=0.60)
    assert score == 1.0
    assert reason is None


def test_experience_combines_years_and_qualitative() -> None:
    # Years met (1.0) averaged with calibrated qualitative (0.35 -> 0.5) = 0.75.
    score, reason = experience_match(5.0, 3.0, qualitative_similarity=0.35)
    assert math.isclose(score, 0.75)
    assert reason is not None


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


def test_education_gate_passes_when_level_and_field_both_strong() -> None:
    # Level met (1.0) and a strong course similarity (calibrated 1.0): the gate
    # (min) passes and the applicant's level is credited as a matched key.
    score, reason = education_match("Bachelor's Degree", ["High School"], course_similarity=0.55)
    assert math.isclose(score, 1.0)
    assert reason == "Bachelor's Degree"


def test_education_course_only_when_no_level_requirement() -> None:
    # No recognizable level requirement: score is the calibrated course match.
    score, reason = education_match(None, [], course_similarity=0.35)
    assert math.isclose(score, 0.5)  # midpoint of the [0.15, 0.55] MiniLM band
    assert reason is None


def test_education_unrelated_field_gates_out_a_met_level() -> None:
    # Level met but an unrelated course (low similarity): the gate takes the
    # field score, so a met level cannot mask the mismatch. No reason is
    # credited since the requirement as a whole is not met.
    score, reason = education_match("Bachelor's Degree", ["High School"], course_similarity=0.15)
    assert math.isclose(score, 0.0)  # min(1.0, calibrated(0.15)=0.0)
    assert reason is None


def test_education_field_below_met_gate_yields_no_reason() -> None:
    # A related-but-not-strong field keeps the score below the "Met" gate, so the
    # degree is not surfaced as a matched key even though the level is met.
    score, reason = education_match("Bachelor's Degree", ["High School"], course_similarity=0.39)
    assert score < 0.8
    assert reason is None


# --- Location --------------------------------------------------------------


def test_location_matches_preference() -> None:
    score, matched = location_match(["Cagayan de Oro"], "Cagayan de Oro City")
    assert score == 1.0
    assert matched == "Cagayan de Oro"


def test_location_matches_on_shared_city_component() -> None:
    # Real-world case: applicant's fully-qualified preference shares the city
    # component with a job whose location leads with a barangay.
    score, matched = location_match(
        ["Cagayan de Oro City, Misamis Oriental"],
        "Bulua, Cagayan de Oro, Misamis Oriental",
    )
    assert score == 1.0
    assert matched == "Cagayan de Oro City, Misamis Oriental"


def test_location_matches_free_text_preference_without_commas() -> None:
    # Reported case: the applicant typed the preference as free text (no commas),
    # so it is one token — but the barangay/city still line up with the job's
    # comma-qualified location via whole-word containment.
    score, matched = location_match(
        ["Kauswagan Cagayan de Oro"],
        "Kauswagan, Cagayan de Oro City, Misamis Oriental",
    )
    assert score == 1.0
    assert matched == "Kauswagan Cagayan de Oro"


def test_location_free_text_word_boundary_not_a_substring_hit() -> None:
    # Containment is whole-word: "oro" must not match inside "toronto".
    assert location_match(["Toronto"], "Oro, Misamis Oriental") == (0.0, None)


def test_location_shared_province_only_is_partial_credit() -> None:
    # Same province (Misamis Oriental) but different city → graded partial score.
    score, matched = location_match(
        ["El Salvador City, Misamis Oriental"],
        "Bulua, Cagayan de Oro, Misamis Oriental",
    )
    assert score == 0.6
    assert matched == "El Salvador City, Misamis Oriental"


def test_location_city_match_beats_province_match() -> None:
    # A city-level match (Cagayan de Oro) outranks a province-only one and is
    # the preference returned, regardless of ordering.
    score, matched = location_match(
        ["El Salvador City, Misamis Oriental", "Cagayan de Oro City, Misamis Oriental"],
        "Bulua, Cagayan de Oro, Misamis Oriental",
    )
    assert score == 1.0
    assert matched == "Cagayan de Oro City, Misamis Oriental"


def test_location_picks_best_scoring_preference() -> None:
    # Cebu (no overlap) is skipped; Alubijid shares only the province → 0.6.
    score, matched = location_match(
        ["Cebu City, Cebu", "Alubijid, Misamis Oriental"],
        "Bulua, Cagayan de Oro, Misamis Oriental",
    )
    assert score == 0.6
    assert matched == "Alubijid, Misamis Oriental"


def test_location_shared_barangay_across_provinces_no_credit() -> None:
    # "Poblacion" is a common barangay name; a collision across different
    # provinces must not earn credit.
    assert location_match(
        ["Poblacion, Davao City, Davao del Sur"],
        "Poblacion, Cagayan de Oro, Misamis Oriental",
    ) == (0.0, None)


def test_location_no_match() -> None:
    assert location_match(["Manila"], "Cebu City") == (0.0, None)
    # Different province and city — no shared component.
    assert location_match(["Makati City, Metro Manila"], "Cebu City, Cebu") == (0.0, None)


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


def test_applicant_experience_text_is_work_only() -> None:
    # Experience text covers work roles/companies and deliberately excludes the
    # course of study — that is scored by the education dimension instead.
    text = applicant_experience_text(_applicant())  # type: ignore[arg-type]
    assert "backend developer" in text
    assert "computer science" not in text


def test_applicant_course_text_strips_degree_framing() -> None:
    text = applicant_course_text(  # type: ignore[arg-type]
        _applicant(
            educational_background=SimpleNamespace(
                highest_education_level="Bachelor's Degree",
                course_program="Bachelor of Science in Information Technology",
                school_university="Liceo",
            )
        )
    )
    assert "information technology" in text
    assert "bachelor" not in text  # degree scaffolding removed


def test_applicant_course_text_empty_without_course() -> None:
    assert applicant_course_text(_applicant(educational_background=None)) == ""  # type: ignore[arg-type]


def test_job_course_text_strips_degree_framing() -> None:
    job = SimpleNamespace(course_program="BS Information Technology")
    assert job_course_text(job) == "information technology"  # type: ignore[arg-type]


def test_job_course_text_empty_without_course() -> None:
    assert job_course_text(SimpleNamespace(course_program=None)) == ""  # type: ignore[arg-type]


def test_job_to_text_is_preprocessed() -> None:
    job = SimpleNamespace(
        title="Senior Backend Engineer",
        description="Build APIs.",
        skills_required=["Python", "MongoDB"],
        experience_required="3 years",
        minimum_education_attainment=["Bachelor's Degree"],
        course_program="BS Computer Science",
    )
    text = job_to_text(job)  # type: ignore[arg-type]
    assert "senior backend engineer" in text
    assert "mongodb" in text
    assert "computer science" in text
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


def test_embed_batch_empty_short_circuits_without_calling_api() -> None:
    from app.matching import embeddings

    assert embeddings.embed_batch([]) == []
    assert embeddings._client is None  # still lazy — empty batch never opens a client


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
