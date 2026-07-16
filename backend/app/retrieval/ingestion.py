"""Deterministic, idempotent policy ingestion.

For each canonical source (existing DB policy versions + isolated fixture files) this
upserts the policy version, and reindexes its chunks only when the source content or the
index provenance (chunker / embedding provider / model / dimension / schema) changed, or
when a reindex is forced. Unchanged versions are never silently deleted and recreated.
Runs in a single transaction; a failure rolls back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.policy import Policy, PolicyChunk, PolicyVersion
from app.retrieval.chunker import chunk_markdown
from app.retrieval.constants import CHUNKER_VERSION, INDEX_SCHEMA_VERSION
from app.retrieval.embeddings import EmbeddingProvider
from app.retrieval.sources import (
    PolicySource,
    fixture_sources,
    source_type_for_topic,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")[:48] or "section"


def citation_id(topic: str, version: int, section_path: str, chunk_index: int) -> str:
    return (
        f"POL-{topic.upper()}:v{version}:{_slug(section_path)}:chunk-{chunk_index:02d}"
    )


@dataclass(slots=True)
class VersionReport:
    topic: str
    version: int
    action: str  # created | updated | skipped
    chunk_count: int


@dataclass(slots=True)
class IngestionReport:
    provider: str
    model: str
    dimension: int
    chunker_version: str
    versions: list[VersionReport] = field(default_factory=list)

    @property
    def total_chunks(self) -> int:
        return sum(v.chunk_count for v in self.versions)

    @property
    def reindexed(self) -> int:
        return sum(1 for v in self.versions if v.action != "skipped")


async def _build_sources(session: AsyncSession) -> list[PolicySource]:
    fixtures = fixture_sources()
    fixture_keys = {(s.topic, s.version) for s in fixtures}
    sources: list[PolicySource] = list(fixtures)

    stmt = select(PolicyVersion, Policy).join(Policy)
    for version, policy in (await session.execute(stmt)).all():
        if (policy.topic, version.version) in fixture_keys:
            continue
        sources.append(
            PolicySource(
                topic=policy.topic,
                title=policy.title,
                description=policy.description,
                version=version.version,
                status=version.status,
                source_type=source_type_for_topic(policy.topic),
                effective_from=version.effective_from,
                effective_to=version.effective_to,
                body=version.body,
                source_path=f"data/policies/{policy.topic}.md",
            )
        )
    return sources


async def _upsert_version(
    session: AsyncSession, source: PolicySource
) -> tuple[PolicyVersion, str, str | None]:
    policy = (
        await session.execute(select(Policy).where(Policy.topic == source.topic))
    ).scalar_one_or_none()
    if policy is None:
        policy = Policy(
            topic=source.topic, title=source.title, description=source.description
        )
        session.add(policy)
        await session.flush()

    version = (
        await session.execute(
            select(PolicyVersion).where(
                PolicyVersion.policy_id == policy.id,
                PolicyVersion.version == source.version,
            )
        )
    ).scalar_one_or_none()
    action = "updated"
    old_hash: str | None = None
    if version is None:
        version = PolicyVersion(policy_id=policy.id, version=source.version)
        session.add(version)
        action = "created"
    else:
        old_hash = version.content_hash

    version.status = source.status
    version.body = source.body
    version.effective_from = source.effective_from
    version.effective_to = source.effective_to
    version.source_type = source.source_type
    version.source_path = source.source_path
    version.content_hash = source.content_hash
    await session.flush()
    return version, action, old_hash


def _is_compatible(version: PolicyVersion, provider: EmbeddingProvider) -> bool:
    return (
        version.indexed_at is not None
        and version.chunker_version == CHUNKER_VERSION
        and version.index_schema_version == INDEX_SCHEMA_VERSION
        and version.embedding_provider == provider.name
        and version.embedding_model == provider.model
        and version.embedding_dim == provider.dimension
    )


async def ingest(
    session: AsyncSession,
    provider: EmbeddingProvider,
    *,
    force: bool = False,
) -> IngestionReport:
    report = IngestionReport(
        provider=provider.name,
        model=provider.model,
        dimension=provider.dimension,
        chunker_version=CHUNKER_VERSION,
    )
    for source in await _build_sources(session):
        version, action, old_hash = await _upsert_version(session, source)

        existing_count = (
            (
                await session.execute(
                    select(PolicyChunk.id).where(
                        PolicyChunk.policy_version_id == version.id
                    )
                )
            )
            .scalars()
            .all()
        )

        needs_reindex = (
            force
            or action == "created"
            or not existing_count
            or not _is_compatible(version, provider)
            or old_hash != source.content_hash
        )

        if not needs_reindex:
            report.versions.append(
                VersionReport(
                    source.topic, source.version, "skipped", len(existing_count)
                )
            )
            continue

        await session.execute(
            delete(PolicyChunk).where(PolicyChunk.policy_version_id == version.id)
        )
        specs = chunk_markdown(source.body, title=source.title)
        embeddings = await provider.embed_documents([s.search_text for s in specs])
        for spec, embedding in zip(specs, embeddings, strict=True):
            session.add(
                PolicyChunk(
                    policy_version_id=version.id,
                    chunk_index=spec.chunk_index,
                    section_path=spec.section_path,
                    heading=spec.heading,
                    body=spec.body,
                    search_text=spec.search_text,
                    embedding=embedding,
                    token_count=spec.token_count,
                    character_count=spec.character_count,
                    content_hash=spec.content_hash,
                    citation_id=citation_id(
                        source.topic,
                        source.version,
                        spec.section_path,
                        spec.chunk_index,
                    ),
                )
            )
        version.indexed_at = datetime.now(UTC)
        version.chunker_version = CHUNKER_VERSION
        version.index_schema_version = INDEX_SCHEMA_VERSION
        version.embedding_provider = provider.name
        version.embedding_model = provider.model
        version.embedding_dim = provider.dimension
        await session.flush()
        report.versions.append(
            VersionReport(source.topic, source.version, action, len(specs))
        )

    return report
