"""Frozen constants and stable version identifiers for policy retrieval."""

from __future__ import annotations

from app.models.policy import EMBEDDING_DIM

__all__ = ["CHUNKER_VERSION", "EMBEDDING_DIM", "INDEX_SCHEMA_VERSION", "RRF_K"]

# Stable version tags used to detect incompatible indexes and force reindexing.
CHUNKER_VERSION = "chunker-v1"
INDEX_SCHEMA_VERSION = "retrieval-index-v1"

# Reciprocal Rank Fusion constant (see docs/policy-retrieval.md).
RRF_K = 60
# Lexical is the more precise channel for policy terms; the deterministic hash embedding
# is a weak semantic signal. Lexical is weighted high enough that hybrid never
# underperforms lexical — semantic only fills results lexical missed entirely. With a
# real local embedding provider, lower the lexical weight so semantic contributes more.
LEXICAL_WEIGHT = 3.0
SEMANTIC_WEIGHT = 1.0

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

# Support thresholds. Lexical (ts_rank_cd) presence above this = topical support;
# semantic (cosine similarity) support needs a higher bar because the deterministic hash
# embedding is a weak semantic signal.
DEFAULT_MIN_SUPPORT_SCORE = 0.02
SEMANTIC_SUPPORT_MIN = 0.30
