"""High-level policy retrieval service (independent of FastAPI).

Validates and normalises the query, resolves the allowed source types, checks policy
currency/conflicts (via the S2 policy-validity rule), runs lexical + semantic retrieval,
fuses them, builds stable citations, and runs support checks. It never generates an
answer. Source-type restrictions cannot be widened by an ordinary model-facing caller.
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PolicySourceType
from app.repositories.policy import PolicyRepository
from app.retrieval.constants import (
    CHUNKER_VERSION,
    INDEX_SCHEMA_VERSION,
    MAX_EXCERPT_CHARS,
)
from app.retrieval.embeddings import EmbeddingProvider
from app.retrieval.fusion import FusedCandidate, reciprocal_rank_fusion
from app.retrieval.models import (
    ConflictStatus,
    IndexMetadata,
    PolicyEvidenceItem,
    PolicyRetrievalRequest,
    PolicyRetrievalResult,
    RetrievalMode,
    SupportStatus,
)
from app.retrieval.query import normalise_query
from app.retrieval.repository import (
    RetrievalCandidate,
    RetrievalFilter,
    RetrievalRepository,
)
from app.retrieval.support import SupportInputs, determine_support
from app.rules.clock import Clock, FixedClock
from app.rules.enums import ReasonCode
from app.rules.models import RuleResult
from app.rules.policies import PolicyVersionFact, validate_policy_versions


class PolicyRetrievalService:
    def __init__(self, session: AsyncSession, provider: EmbeddingProvider) -> None:
        self.session = session
        self.repo = RetrievalRepository(session)
        self.policies = PolicyRepository(session)
        self.provider = provider

    def _default_source_types(
        self, include_historical: bool
    ) -> frozenset[PolicySourceType]:
        allowed = {PolicySourceType.official_policy}
        if include_historical:
            allowed.add(PolicySourceType.historical_policy)
        return frozenset(allowed)

    async def _check_topic(
        self, topic: str, as_of_clock: Clock
    ) -> tuple[bool, bool, RuleResult]:
        versions = await self.policies.find_versions_by_topic(topic)
        facts = [
            PolicyVersionFact(
                policy_id=v.policy_id,
                policy_version_id=v.id,
                topic=topic,
                version=v.version,
                status=v.status,
                effective_from=v.effective_from,
                effective_to=v.effective_to,
            )
            for v in versions
        ]
        validation = validate_policy_versions(topic, facts, as_of_clock)
        conflict = validation.has(ReasonCode.POLICY_CONFLICT)
        has_active = validation.has(ReasonCode.POLICY_ACTIVE)
        return conflict, has_active, validation

    async def retrieve(
        self,
        request: PolicyRetrievalRequest,
        *,
        clock: Clock,
        source_types: frozenset[PolicySourceType] | None = None,
    ) -> PolicyRetrievalResult:
        start = time.perf_counter()
        correlation_id = request.correlation_id or uuid.uuid4().hex
        normalised = normalise_query(request.query)
        as_of = request.as_of or clock.now()
        as_of_clock = FixedClock(as_of)
        allowed = source_types or self._default_source_types(request.include_historical)
        warnings: list[str] = []

        # Policy currency / conflict (authoritative, ignores source-type filtering).
        conflict = False
        topic_has_active = True
        validation: RuleResult | None = None
        if request.topic:
            conflict, topic_has_active, validation = await self._check_topic(
                request.topic, as_of_clock
            )

        retrieval_filter = RetrievalFilter(
            source_types=allowed,
            as_of=as_of.date(),
            include_historical=request.include_historical,
            topic=request.topic,
            required_policy_ids=tuple(request.required_policy_ids),
            excluded_policy_ids=tuple(request.excluded_policy_ids),
        )

        want_semantic = request.mode in (RetrievalMode.semantic, RetrievalMode.hybrid)
        want_lexical = request.mode in (RetrievalMode.lexical, RetrievalMode.hybrid)
        degraded = False

        lexical: list[RetrievalCandidate] = []
        semantic: list[RetrievalCandidate] = []
        if want_lexical:
            lexical = await self.repo.lexical_search(
                normalised, retrieval_filter, limit=request.candidate_limit
            )
        if want_semantic:
            try:
                embedding = await self.provider.embed_query(normalised)
                semantic = await self.repo.vector_search(
                    embedding, retrieval_filter, limit=request.candidate_limit
                )
            except Exception:  # optional provider unavailable -> lexical-only
                degraded = True
                warnings.append(
                    "Semantic retrieval unavailable; lexical-only fallback."
                )
                if not want_lexical:  # semantic-only request must still fall back
                    lexical = await self.repo.lexical_search(
                        normalised, retrieval_filter, limit=request.candidate_limit
                    )

        fused = reciprocal_rank_fusion(lexical, semantic)[: request.top_k]

        mode_used = request.mode
        if degraded and request.mode in (RetrievalMode.semantic, RetrievalMode.hybrid):
            mode_used = RetrievalMode.lexical

        evidence = [
            self._to_evidence(fc, rank, mode_used)
            for rank, fc in enumerate(fused, start=1)
        ]

        represented = frozenset(str(fc.candidate.policy.id) for fc in fused)
        support = determine_support(
            SupportInputs(
                evidence=fused,
                min_lexical_score=request.min_support_score,
                topic_known=request.topic is not None,
                topic_has_active_policy=topic_has_active,
                conflict=conflict,
                required_policy_ids=frozenset(
                    str(i) for i in request.required_policy_ids
                ),
                represented_policy_ids=represented,
            )
        )
        if degraded and not fused:
            support = SupportStatus.retrieval_degraded
        if conflict:
            warnings.append("Conflicting active policy versions detected.")

        duration_ms = int((time.perf_counter() - start) * 1000)
        return PolicyRetrievalResult(
            query=request.query,
            normalised_query=normalised,
            mode_requested=request.mode,
            mode_used=mode_used,
            evidence=evidence,
            policy_validation=validation,
            conflict_status=ConflictStatus.conflicting
            if conflict
            else ConflictStatus.none,
            support_status=support,
            warnings=warnings,
            index_metadata=IndexMetadata(
                embedding_provider=self.provider.name,
                embedding_model=self.provider.model,
                embedding_dim=self.provider.dimension,
                chunker_version=CHUNKER_VERSION,
                index_schema_version=INDEX_SCHEMA_VERSION,
                degraded=degraded,
            ),
            duration_ms=duration_ms,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _to_evidence(
        fc: FusedCandidate, hybrid_rank: int, mode_used: RetrievalMode
    ) -> PolicyEvidenceItem:
        chunk = fc.candidate.chunk
        policy = fc.candidate.policy
        version = fc.candidate.version
        return PolicyEvidenceItem(
            citation_id=chunk.citation_id,
            policy_id=policy.id,
            policy_version_id=version.id,
            policy_title=policy.title,
            topic=policy.topic,
            version=version.version,
            status=version.status,
            effective_from=version.effective_from,
            effective_to=version.effective_to,
            chunk_id=chunk.id,
            section_path=chunk.section_path,
            heading=chunk.heading,
            excerpt=chunk.body[:MAX_EXCERPT_CHARS],
            content_hash=chunk.content_hash,
            lexical_rank=fc.lexical_rank,
            lexical_score=fc.lexical_score,
            semantic_rank=fc.semantic_rank,
            semantic_score=fc.semantic_score,
            hybrid_rank=hybrid_rank,
            hybrid_score=fc.hybrid_score,
            retrieval_mode=mode_used,
        )
