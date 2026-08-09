"""Semantic embedding generation for the job-matching AI pipeline (Figure 11).

Embeddings are produced by a hosted inference endpoint serving the paper's
selected model (`sentence-transformers/all-MiniLM-L6-v2`) rather than loading
the model locally — torch and its CUDA wheels are far too large for a serverless
bundle. The endpoint returns the same mean-pooled sentence vectors, so the
calibrated cosine bands in ``scoring.py`` need no re-tuning.

The public interface (``embed`` / ``embed_batch``) is unchanged and stays
synchronous, so existing (async) call sites and their test stubs keep working.
A single ``httpx.Client`` is created lazily and cached process-wide.
"""

from threading import Lock
from typing import Any

import httpx
import numpy as np

from app.core.config import settings

_client: httpx.Client | None = None
_lock = Lock()


class EmbeddingError(RuntimeError):
    """Raised when the embedding endpoint cannot be reached or returns an error."""


def _get_client() -> httpx.Client:
    """Return the process-wide HTTP client, creating it on first use.

    Deferred so that importing this module (and collecting tests) never opens a
    connection. Guarded by a lock for the double-checked initialization.
    """
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                headers = {"Content-Type": "application/json"}
                if settings.EMBEDDING_API_TOKEN:
                    headers["Authorization"] = f"Bearer {settings.EMBEDDING_API_TOKEN}"
                _client = httpx.Client(
                    headers=headers,
                    timeout=settings.EMBEDDING_API_TIMEOUT,
                )
    return _client


def _to_sentence_vector(item: Any) -> list[float]:
    """Coerce one endpoint result into a flat sentence vector.

    Sentence-transformers feature-extraction returns a 1-D vector per input.
    Some backends return token-level (2-D) embeddings; mean-pool those so the
    output shape matches what the local model produced.
    """
    arr = np.asarray(item, dtype=float)
    if arr.ndim == 2:
        arr = arr.mean(axis=0)
    return [float(value) for value in arr.ravel()]


def embed(text: str) -> list[float]:
    """Encode a single text into a dense embedding vector."""
    return embed_batch([text])[0]


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Encode a batch of texts into dense embedding vectors.

    Short-circuits on an empty batch without opening a client or calling the API.
    """
    if not texts:
        return []
    try:
        response = _get_client().post(
            settings.EMBEDDING_API_URL,
            json={"inputs": texts, "options": {"wait_for_model": True}},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise EmbeddingError(
                "Embedding endpoint rejected the request "
                f"({status}). Set EMBEDDING_API_TOKEN to a valid token for "
                f"{settings.EMBEDDING_API_URL} (a Hugging Face read token by default)."
            ) from exc
        raise EmbeddingError(
            f"Embedding endpoint returned HTTP {status} for "
            f"{settings.EMBEDDING_API_URL}: {exc.response.text[:200]}"
        ) from exc
    except httpx.HTTPError as exc:
        raise EmbeddingError(
            f"Could not reach embedding endpoint {settings.EMBEDDING_API_URL}: {exc}"
        ) from exc
    payload = response.json()
    return [_to_sentence_vector(item) for item in payload]
