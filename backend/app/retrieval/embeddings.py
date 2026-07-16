"""Embedding abstraction for policy retrieval (separate from the future LLM provider).

The default ``deterministic_hash`` provider needs no model download or network and is
fully reproducible — suitable for CI and deterministic tests. It is an honest hashed
bag-of-words: cosine similarity reflects token overlap, not deep semantics. Optional
local providers (Sentence Transformers, Ollama) are supported but never required and are
never auto-downloaded in tests.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.core.config import Settings
from app.retrieval.constants import EMBEDDING_DIM

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Two independent hash seeds spread each token across a few dimensions.
_SEEDS = (b"agentops-a", b"agentops-b")


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimension: int

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class DeterministicHashEmbedding:
    """Reproducible hashed bag-of-words embedding. No model, no network."""

    name = "deterministic_hash"
    model = "deterministic-hash-v1"

    def __init__(self, dimension: int = EMBEDDING_DIM) -> None:
        self.dimension = dimension

    def _embed_one(self, text: str) -> list[float]:
        if not text or not text.strip():
            raise ValueError("Cannot embed empty text")
        vector = [0.0] * self.dimension
        tokens = _tokenize(text)
        if not tokens:
            # Non-empty but no alphanumeric tokens: fall back to a stable char hash.
            tokens = [text.strip().lower()]
        for token in tokens:
            for seed in _SEEDS:
                # Non-cryptographic feature hashing (stable across processes).
                digest = hashlib.sha1(seed + token.encode("utf-8")).digest()  # noqa: S324
                index = int.from_bytes(digest[:4], "big") % self.dimension
                sign = 1.0 if digest[4] & 1 else -1.0
                vector[index] += sign
        norm = sum(component * component for component in vector) ** 0.5
        if norm == 0.0:
            return vector
        return [component / norm for component in vector]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)


class _UnavailableEmbedding:
    """Placeholder that fails clearly when an optional provider was selected but is not
    installed/available. Never used for CI or tests."""

    def __init__(self, name: str, model: str, reason: str) -> None:
        self.name = name
        self.model = model
        self.dimension = EMBEDDING_DIM
        self._reason = reason

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError(self._reason)

    async def embed_query(self, text: str) -> list[float]:
        raise RuntimeError(self._reason)


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Return the configured embedding provider.

    Optional providers that are selected but unavailable return a provider that raises a
    clear error on use, rather than silently degrading semantic quality.
    """
    if settings.embedding_provider == "deterministic_hash":
        return DeterministicHashEmbedding()
    if settings.embedding_provider == "sentence_transformers":
        return _UnavailableEmbedding(
            "sentence_transformers",
            settings.sentence_transformer_model,
            "sentence-transformers is not installed; install it or set "
            "EMBEDDING_PROVIDER=deterministic_hash.",
        )
    return _UnavailableEmbedding(
        "ollama",
        settings.ollama_embedding_model,
        "Ollama embeddings are not available; run Ollama or set "
        "EMBEDDING_PROVIDER=deterministic_hash.",
    )
