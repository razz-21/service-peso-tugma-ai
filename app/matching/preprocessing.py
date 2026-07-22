"""Text preprocessing for the job-matching AI pipeline (Figure 10).

Implements the paper's preprocessing procedure applied to resume and job
description text before semantic embedding: text cleaning -> lowercasing ->
stopword removal -> normalization. Producing clean, consistent text improves the
quality of the semantic embeddings and the reliability of similarity scoring.
"""

import re

# A compact English stopword list, kept inline to avoid pulling a heavyweight NLP
# dependency (e.g. nltk) for what is a small, fixed set of function words.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        "when",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "as",
        "by",
        "at",
        "from",
        "into",
        "about",
        "against",
        "between",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "do",
        "does",
        "did",
        "doing",
        "have",
        "has",
        "had",
        "having",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "them",
        "this",
        "that",
        "these",
        "those",
        "my",
        "your",
        "our",
        "their",
        "its",
        "his",
        "her",
        "will",
        "would",
        "can",
        "could",
        "should",
        "shall",
        "may",
        "might",
        "must",
        "not",
        "no",
        "nor",
        "so",
        "than",
        "too",
        "very",
        "s",
        "t",
        "just",
        "also",
        "which",
        "who",
        "whom",
        "what",
        "where",
        "why",
        "how",
        "all",
        "any",
        "both",
        "each",
        "more",
        "most",
        "other",
        "some",
        "such",
        "only",
        "own",
        "same",
    }
)

# Strips punctuation and special symbols (anything that is neither a word
# character nor whitespace); `_WHITESPACE` collapses runs of spaces for
# normalization.
_NON_WORD = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def clean(text: str) -> str:
    """Replace punctuation/special characters with spaces, keeping words."""
    return _NON_WORD.sub(" ", text)


def remove_stopwords(tokens: list[str]) -> list[str]:
    """Drop common function words that carry little matching signal."""
    return [token for token in tokens if token not in _STOPWORDS]


def preprocess(text: str) -> str:
    """Run the full preprocessing pipeline and return normalized text.

    Order follows Figure 10: clean -> lowercase -> stopword removal ->
    normalization (collapse whitespace, trim). Returns an empty string for
    empty or whitespace-only input.
    """
    if not text:
        return ""
    lowered = clean(text).lower()
    tokens = [token for token in _WHITESPACE.sub(" ", lowered).strip().split(" ") if token]
    return " ".join(remove_stopwords(tokens))
