"""Job-matching AI pipeline (paper Chapter 3, Figure 7).

Pipeline stages: text preprocessing -> semantic embedding -> cosine similarity +
rule-based ranking -> weighted combined MatchScore. The embedding model is heavy
(pulls torch), so `embeddings` is intentionally not re-exported here — import it
explicitly (`from app.matching.embeddings import embed`) so that `import
app.matching` stays lightweight for the pure-Python scoring/preprocessing paths.
"""

from .preprocessing import clean, preprocess, remove_stopwords
from .profile import applicant_experience_years, applicant_to_text, job_to_text
from .scoring import (
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

__all__ = [
    "DEFAULT_WEIGHTS",
    "MatchWeights",
    "ScoreBreakdown",
    "applicant_experience_years",
    "applicant_to_text",
    "clean",
    "combined_score",
    "cosine_similarity",
    "education_match",
    "experience_match",
    "job_to_text",
    "location_match",
    "parse_required_years",
    "preprocess",
    "remove_stopwords",
    "skills_match",
]
