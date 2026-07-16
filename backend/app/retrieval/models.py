"""Typed retrieval request, evidence, and result models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.models.enums import PolicyStatus
from app.retrieval.constants import (
    DEFAULT_MIN_SUPPORT_SCORE,
    DEFAULT_TOP_K,
    MAX_CANDIDATES,
    MAX_TOP_K,
)
from app.rules.models import RuleResult


class RetrievalMode(StrEnum):
    lexical = "lexical"
    semantic = "semantic"
    hybrid = "hybrid"


class SupportStatus(StrEnum):
    supported = "supported"
    partially_supported = "partially_supported"
    unsupported = "unsupported"
    conflicting = "conflicting"
    no_active_policy = "no_active_policy"
    retrieval_degraded = "retrieval_degraded"


class ConflictStatus(StrEnum):
    none = "none"
    conflicting = "conflicting"


class PolicyRetrievalRequest(BaseModel):
    query: str = Field(min_length=1)
    topic: str | None = None
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    candidate_limit: int = Field(default=MAX_CANDIDATES, ge=1, le=MAX_CANDIDATES)
    mode: RetrievalMode = RetrievalMode.hybrid
    as_of: datetime | None = None
    include_historical: bool = False
    required_policy_ids: list[uuid.UUID] = Field(default_factory=list)
    excluded_policy_ids: list[uuid.UUID] = Field(default_factory=list)
    min_support_score: float = DEFAULT_MIN_SUPPORT_SCORE
    correlation_id: str | None = None

    @field_validator("as_of")
    @classmethod
    def _tz_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        return value


class PolicyEvidenceItem(BaseModel):
    citation_id: str
    policy_id: uuid.UUID
    policy_version_id: uuid.UUID
    policy_title: str
    topic: str
    version: int
    status: PolicyStatus
    effective_from: date
    effective_to: date | None
    chunk_id: uuid.UUID
    section_path: str
    heading: str | None
    excerpt: str
    content_hash: str
    lexical_rank: int | None = None
    lexical_score: float | None = None
    semantic_rank: int | None = None
    semantic_score: float | None = None
    hybrid_rank: int
    hybrid_score: float
    retrieval_mode: RetrievalMode


class IndexMetadata(BaseModel):
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    chunker_version: str
    index_schema_version: str
    degraded: bool = False


class PolicyRetrievalResult(BaseModel):
    query: str
    normalised_query: str
    mode_requested: RetrievalMode
    mode_used: RetrievalMode
    evidence: list[PolicyEvidenceItem]
    policy_validation: RuleResult | None
    conflict_status: ConflictStatus
    support_status: SupportStatus
    warnings: list[str]
    index_metadata: IndexMetadata
    duration_ms: int
    correlation_id: str
