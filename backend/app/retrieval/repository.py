"""Retrieval repositories: lexical (full-text) and semantic (pgvector) candidate search,
plus policy-chunk lookups. Source-trust and active-version filters are applied in the
query so a caller can never widen them by post-processing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PolicySourceType, PolicyStatus
from app.models.policy import Policy, PolicyChunk, PolicyVersion
from app.retrieval.query import to_or_tsquery_expr


@dataclass(slots=True)
class RetrievalCandidate:
    chunk: PolicyChunk
    policy: Policy
    version: PolicyVersion
    score: float


@dataclass(frozen=True, slots=True)
class RetrievalFilter:
    source_types: frozenset[PolicySourceType]
    as_of: date
    include_historical: bool = False
    topic: str | None = None
    required_policy_ids: tuple[uuid.UUID, ...] = ()
    excluded_policy_ids: tuple[uuid.UUID, ...] = ()


def _predicates(f: RetrievalFilter) -> list[ColumnElement[bool]]:
    preds: list[ColumnElement[bool]] = [
        PolicyVersion.is_retrieval_enabled.is_(True),
        PolicyVersion.source_type.in_(tuple(f.source_types)),
    ]
    if not f.include_historical:
        preds.append(PolicyVersion.status == PolicyStatus.active)
        preds.append(PolicyVersion.effective_from <= f.as_of)
        preds.append(
            or_(
                PolicyVersion.effective_to.is_(None),
                PolicyVersion.effective_to >= f.as_of,
            )
        )
    if f.topic is not None:
        preds.append(Policy.topic == f.topic)
    if f.required_policy_ids:
        preds.append(Policy.id.in_(f.required_policy_ids))
    if f.excluded_policy_ids:
        preds.append(Policy.id.notin_(f.excluded_policy_ids))
    return preds


def _base_select() -> Select[tuple[PolicyChunk, Policy, PolicyVersion]]:
    return (
        select(PolicyChunk, Policy, PolicyVersion)
        .join(PolicyVersion, PolicyChunk.policy_version_id == PolicyVersion.id)
        .join(Policy, PolicyVersion.policy_id == Policy.id)
    )


class RetrievalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lexical_search(
        self, query: str, f: RetrievalFilter, *, limit: int
    ) -> list[RetrievalCandidate]:
        or_expr = to_or_tsquery_expr(query)
        if not or_expr:
            return []
        tsquery = func.to_tsquery("english", or_expr)
        # Normalization flag 1 divides rank by 1 + log(document length) to reduce the
        # bias toward longer chunks (e.g. the broad privacy policy) under OR matching.
        score = func.ts_rank_cd(PolicyChunk.search_vector, tsquery, 1)
        stmt = (
            _base_select()
            .add_columns(score.label("score"))
            .where(and_(*_predicates(f), PolicyChunk.search_vector.op("@@")(tsquery)))
            .order_by(score.desc(), PolicyChunk.citation_id.asc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            RetrievalCandidate(chunk, policy, version, float(s))
            for chunk, policy, version, s in rows
        ]

    async def vector_search(
        self, embedding: list[float], f: RetrievalFilter, *, limit: int
    ) -> list[RetrievalCandidate]:
        distance = PolicyChunk.embedding.cosine_distance(embedding)
        stmt = (
            _base_select()
            .add_columns(distance.label("distance"))
            .where(and_(*_predicates(f), PolicyChunk.embedding.isnot(None)))
            .order_by(distance.asc(), PolicyChunk.citation_id.asc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        # Cosine similarity in [0, 1] (documented score transform: 1 - cosine_distance).
        return [
            RetrievalCandidate(chunk, policy, version, max(0.0, 1.0 - float(d)))
            for chunk, policy, version, d in rows
        ]

    async def get_by_citation_id(self, citation_id: str) -> RetrievalCandidate | None:
        stmt = _base_select().where(PolicyChunk.citation_id == citation_id)
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return None
        chunk, policy, version = row
        return RetrievalCandidate(chunk, policy, version, 0.0)

    async def count_active_official_with_embeddings(self) -> int:
        stmt = (
            select(func.count(PolicyChunk.id))
            .join(PolicyVersion, PolicyChunk.policy_version_id == PolicyVersion.id)
            .where(
                PolicyVersion.source_type == PolicySourceType.official_policy,
                PolicyVersion.status == PolicyStatus.active,
                PolicyChunk.embedding.isnot(None),
            )
        )
        return (await self.session.execute(stmt)).scalar_one()
