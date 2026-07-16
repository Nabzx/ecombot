"""Frozen constants and stable version identifiers for policy retrieval."""

from __future__ import annotations

from app.models.policy import EMBEDDING_DIM

__all__ = ["CHUNKER_VERSION", "EMBEDDING_DIM", "INDEX_SCHEMA_VERSION", "RRF_K"]

# Stable version tags used to detect incompatible indexes and force reindexing.
CHUNKER_VERSION = "chunker-v1"
INDEX_SCHEMA_VERSION = "retrieval-index-v1"

# Reciprocal Rank Fusion constant (see docs/policy-retrieval.md).
RRF_K = 60

# Chunking limits (characters).
CHUNK_MAX_CHARS = 800
CHUNK_MIN_CHARS = 120

# Request / safety limits.
MAX_QUERY_CHARS = 512
MAX_TOP_K = 20
MAX_CANDIDATES = 50
MAX_EXCERPT_CHARS = 600
DEFAULT_TOP_K = 5
EMBED_BATCH_SIZE = 64

# Default minimum hybrid score for a result to count as supporting evidence.
DEFAULT_MIN_SUPPORT_SCORE = 0.010
