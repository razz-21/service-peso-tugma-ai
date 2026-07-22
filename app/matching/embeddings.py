"""Semantic embedding generation for the job-matching AI pipeline (Figure 11).

Wraps a transformer-based sentence-embedding model (default
`sentence-transformers/all-MiniLM-L12-v2`, the paper's selected model) and
exposes helpers to encode text into fixed-length dense vectors. The model is
loaded lazily on first use and cached as a process-wide singleton, so importing
this module (and collecting tests) does not download or load the model.
"""

from threading import Lock

from sentence_transformers import SentenceTransformer

from app.core.config import settings

_model: SentenceTransformer | None = None
_lock = Lock()


def get_model() -> SentenceTransformer:
    """Return the process-wide embedding model, loading it on first use.

    Loading downloads the model on first run and holds it in memory, so it is
    deferred until the first embedding is actually requested. Guarded by a lock
    for the double-checked initialization.
    """
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                _model = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
    return _model


def embed(text: str) -> list[float]:
    """Encode a single text into a dense embedding vector."""
    return embed_batch([text])[0]


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Encode a batch of texts into dense embedding vectors.

    Short-circuits on an empty batch without loading the model.
    """
    if not texts:
        return []
    vectors = get_model().encode(texts, convert_to_numpy=True, normalize_embeddings=False)
    return [[float(value) for value in vector] for vector in vectors]
