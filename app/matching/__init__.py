"""Job-matching AI pipeline (paper Chapter 3, Figure 7).

Pipeline stages: text preprocessing -> semantic embedding -> cosine similarity +
rule-based ranking -> weighted combined MatchScore. The embedding model is heavy
(pulls torch), so `embeddings` is intentionally not re-exported here — import it
explicitly (`from app.matching.embeddings import embed`) so that `import
app.matching` stays lightweight for the pure-Python scoring/preprocessing paths.
"""

from .preprocessing import clean, preprocess, remove_stopwords, strip_degree_framing
from .primary_requirements import (
    age_matches,
    applicant_age,
    civil_status_matches,
    eligibility_matches,
    has_open_vacancy,
    meets_primary_requirements,
    parse_age_range,
    sex_matches,
)
from .profile import (
    applicant_experience_text,
    applicant_experience_years,
    applicant_to_text,
    job_to_text,
)
from .scoring import (
    DEFAULT_WEIGHTS,
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

__all__ = [
    "DEFAULT_WEIGHTS",
    "MatchWeights",
    "ScoreBreakdown",
    "age_matches",
    "applicant_age",
    "applicant_experience_text",
    "applicant_experience_years",
    "applicant_to_text",
    "civil_status_matches",
    "clean",
    "combined_score",
    "cosine_similarity",
    "education_match",
    "eligibility_matches",
    "experience_match",
    "experience_requirement_terms",
    "has_open_vacancy",
    "job_to_text",
    "meets_primary_requirements",
    "location_match",
    "parse_age_range",
    "parse_required_years",
    "preprocess",
    "remove_stopwords",
    "sex_matches",
    "strip_degree_framing",
    "skills_match",
]
