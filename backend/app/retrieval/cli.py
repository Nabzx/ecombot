"""CLI for policy indexing and (from later groups) search and evaluation.

python -m app.retrieval.cli index
python -m app.retrieval.cli reindex --yes
python -m app.retrieval.cli stats
python -m app.retrieval.cli verify
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import dispose_engine, get_sessionmaker
from app.models.enums import PolicySourceType, PolicyStatus
from app.models.policy import Policy, PolicyChunk, PolicyVersion
from app.retrieval.constants import CHUNKER_VERSION, INDEX_SCHEMA_VERSION
from app.retrieval.embeddings import get_embedding_provider
from app.retrieval.ingestion import ingest


async def _run_index(force: bool) -> int:
    settings = get_settings()
    provider = get_embedding_provider(settings)
    async with get_sessionmaker()() as session:
        report = await ingest(session, provider, force=force)
        await session.commit()
    print(
        f"Indexed with provider={report.provider} model={report.model} "
        f"dim={report.dimension} {report.chunker_version}"
    )
    for version in report.versions:
        print(
            f"  {version.action:<8} {version.topic} v{version.version} "
            f"({version.chunk_count} chunks)"
        )
    print(f"Total chunks: {report.total_chunks}; reindexed: {report.reindexed}")
    return 0


async def _run_stats() -> int:
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(
                    Policy.topic,
                    PolicyVersion.version,
                    PolicyVersion.status,
                    PolicyVersion.source_type,
                    func.count(PolicyChunk.id),
                )
                .join(PolicyVersion, Policy.id == PolicyVersion.policy_id)
                .outerjoin(
                    PolicyChunk, PolicyChunk.policy_version_id == PolicyVersion.id
                )
                .group_by(
                    Policy.topic,
                    PolicyVersion.version,
                    PolicyVersion.status,
                    PolicyVersion.source_type,
                )
                .order_by(Policy.topic, PolicyVersion.version)
            )
        ).all()
    print(f"{'TOPIC':<30}{'VER':<5}{'STATUS':<12}{'SOURCE':<18}CHUNKS")
    total = 0
    for topic, version, status, source_type, count in rows:
        total += count
        print(f"{topic:<30}{version:<5}{status:<12}{source_type:<18}{count}")
    print(f"\nTotal chunks: {total}")
    return 0


async def _run_verify() -> int:
    settings = get_settings()
    provider = get_embedding_provider(settings)
    problems: list[str] = []
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(Policy.topic, PolicyVersion).join(
                    PolicyVersion, Policy.id == PolicyVersion.policy_id
                )
            )
        ).all()
        for topic, version in rows:
            is_official_active = (
                version.source_type == PolicySourceType.official_policy
                and version.status == PolicyStatus.active
                and version.is_retrieval_enabled
            )
            if not is_official_active:
                continue
            chunk_count = (
                await session.execute(
                    select(func.count(PolicyChunk.id)).where(
                        PolicyChunk.policy_version_id == version.id
                    )
                )
            ).scalar_one()
            missing_embeddings = (
                await session.execute(
                    select(func.count(PolicyChunk.id)).where(
                        PolicyChunk.policy_version_id == version.id,
                        PolicyChunk.embedding.is_(None),
                    )
                )
            ).scalar_one()
            if chunk_count == 0:
                problems.append(f"{topic} v{version.version}: not indexed")
            elif missing_embeddings:
                problems.append(f"{topic} v{version.version}: missing embeddings")
            elif (
                version.chunker_version != CHUNKER_VERSION
                or version.index_schema_version != INDEX_SCHEMA_VERSION
                or version.embedding_provider != provider.name
                or version.embedding_dim != provider.dimension
            ):
                problems.append(f"{topic} v{version.version}: incompatible index")

    if problems:
        print("Policy index verification FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Policy index verification passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retrieval", description="Policy retrieval")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index", help="Index policies (idempotent)")
    reindex = sub.add_parser("reindex", help="Force full reindex")
    reindex.add_argument("--yes", action="store_true", help="Confirm reindex")
    sub.add_parser("stats", help="Show index statistics")
    sub.add_parser("verify", help="Verify the policy index (non-zero on failure)")
    args = parser.parse_args(argv)

    async def _run() -> int:
        try:
            if args.command == "index":
                return await _run_index(force=False)
            if args.command == "reindex":
                if not args.yes:
                    print("Refusing to reindex without --yes")
                    return 2
                return await _run_index(force=True)
            if args.command == "stats":
                return await _run_stats()
            if args.command == "verify":
                return await _run_verify()
            return 2
        finally:
            await dispose_engine()

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
