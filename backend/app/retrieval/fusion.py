"""Reciprocal Rank Fusion of lexical and semantic candidate lists.

Lexical (ts_rank_cd) and vector (cosine) scores are not comparable, so ranks are
fused: ``fused = sum(1 / (RRF_K + rank))`` over the channels a chunk is in. Duplicate
chunks are merged; ties break deterministically on citation id.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.constants import LEXICAL_WEIGHT, RRF_K, SEMANTIC_WEIGHT
from app.retrieval.repository import RetrievalCandidate


@dataclass(slots=True)
class FusedCandidate:
    candidate: RetrievalCandidate
    hybrid_score: float
    lexical_rank: int | None = None
    lexical_score: float | None = None
    semantic_rank: int | None = None
    semantic_score: float | None = None


def reciprocal_rank_fusion(
    lexical: list[RetrievalCandidate],
    semantic: list[RetrievalCandidate],
    *,
    k: int = RRF_K,
    lexical_weight: float = LEXICAL_WEIGHT,
    semantic_weight: float = SEMANTIC_WEIGHT,
) -> list[FusedCandidate]:
    fused: dict[str, FusedCandidate] = {}

    def _slot(candidate: RetrievalCandidate) -> FusedCandidate:
        cid = candidate.chunk.citation_id
        if cid not in fused:
            fused[cid] = FusedCandidate(candidate=candidate, hybrid_score=0.0)
        return fused[cid]

    for rank, candidate in enumerate(lexical, start=1):
        slot = _slot(candidate)
        slot.hybrid_score += lexical_weight / (k + rank)
        slot.lexical_rank = rank
        slot.lexical_score = candidate.score

    for rank, candidate in enumerate(semantic, start=1):
        slot = _slot(candidate)
        slot.hybrid_score += semantic_weight / (k + rank)
        slot.semantic_rank = rank
        slot.semantic_score = candidate.score

    return sorted(
        fused.values(),
        key=lambda fc: (-fc.hybrid_score, fc.candidate.chunk.citation_id),
    )
