"""CLI for policy indexing and (from later groups) search and evaluation.

python -m app.retrieval.cli index
python -m app.retrieval.cli reindex --yes
python -m app.retrieval.cli stats
python -m app.retrieval.cli verify
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import dispose_engine, get_sessionmaker
from app.models.enums import PolicySourceType, PolicyStatus
from app.models.policy import Policy, PolicyChunk, PolicyVersion
from app.retrieval.constants import CHUNKER_VERSION, INDEX_SCHEMA_VERSION
from app.retrieval.embeddings import get_embedding_provider
from app.retrieval.ingestion import ingest
from app.retrieval.models import PolicyRetrievalRequest, RetrievalMode
from app.retrieval.repository import RetrievalRepository
from app.retrieval.service import PolicyRetrievalService
from app.rules.clock import seed_reference_clock


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


async def _run_search(query: str, topic: str | None, mode: str) -> int:
    settings = get_settings()
    provider = get_embedding_provider(settings)
    async with get_sessionmaker()() as session:
        service = PolicyRetrievalService(session, provider)
        result = await service.retrieve(
            PolicyRetrievalRequest(query=query, topic=topic, mode=RetrievalMode(mode)),
            clock=seed_reference_clock(),
        )
    print(f"Query:    {result.query}")
    print(
        f"Mode:     requested={result.mode_requested.value} "
        f"used={result.mode_used.value}"
    )
    print(
        f"Support:  {result.support_status.value}   "
        f"Conflict: {result.conflict_status.value}"
    )
    if result.warnings:
        print(f"Warnings: {result.warnings}")
    print("Evidence:")
    for item in result.evidence:
        print(
            f"  #{item.hybrid_rank} {item.citation_id}  "
            f"[{item.topic} v{item.version} {item.status.value}]"
        )
        sem = round(item.semantic_score, 4) if item.semantic_score is not None else None
        print(
            f"     lexical(rank={item.lexical_rank},score={item.lexical_score}) "
            f"semantic(rank={item.semantic_rank},score={sem}) "
            f"hybrid={round(item.hybrid_score, 5)}"
        )
        print(f"     section: {item.section_path}")
        print(f"     excerpt: {item.excerpt[:160].strip()!r}")
    return 0


async def _run_eval() -> int:
    from dataclasses import asdict
    from datetime import UTC, datetime

    from app.core.paths import get_data_dir
    from app.retrieval.evaluation import evaluate, hard_gate_failures

    settings = get_settings()
    provider = get_embedding_provider(settings)
    async with get_sessionmaker()() as session:
        metrics = await evaluate(session, provider)

    for mode, m in metrics.items():
        print(
            f"[{mode:8}] R@1={m.recall_at_1:.2f} R@3={m.recall_at_3:.2f} "
            f"R@5={m.recall_at_5:.2f} MRR={m.mrr:.2f} topic={m.topic_accuracy:.2f} "
            f"unsup_rej={m.unsupported_rejection:.2f} "
            f"active={m.active_version_accuracy:.2f} "
            f"conflict={m.conflict_detection:.2f} "
            f"hostile_excl={m.hostile_exclusion:.2f} p95={m.p95_latency_ms}ms"
        )

    summary = {
        mode: {k: v for k, v in asdict(m).items() if k != "outcomes"}
        for mode, m in metrics.items()
    }
    reports_dir = get_data_dir().parent / "evaluations" / "reports" / "retrieval"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (reports_dir / f"retrieval_eval_{stamp}.json").write_text(
        json.dumps({"generated_at": stamp, "modes": summary}, indent=2) + "\n"
    )

    fails = hard_gate_failures(metrics["hybrid"])
    if fails:
        print(f"HARD GATE FAILURES (hybrid): {fails}")
        return 1
    print("Hard gates passed (active-version, conflict, hostile-exclusion = 1.00).")
    return 0


async def _run_show_citation(citation_id: str) -> int:
    async with get_sessionmaker()() as session:
        candidate = await RetrievalRepository(session).get_by_citation_id(citation_id)
    if candidate is None:
        print(f"No chunk with citation id {citation_id}")
        return 1
    chunk, policy, version = candidate.chunk, candidate.policy, candidate.version
    print(f"Citation: {chunk.citation_id}")
    print(
        f"Policy:   {policy.title} ({policy.topic}) "
        f"v{version.version} {version.status.value}"
    )
    print(f"Effective:{version.effective_from} -> {version.effective_to}")
    print(f"Section:  {chunk.section_path}")
    print(f"Body:\n{chunk.body}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retrieval", description="Policy retrieval")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index", help="Index policies (idempotent)")
    reindex = sub.add_parser("reindex", help="Force full reindex")
    reindex.add_argument("--yes", action="store_true", help="Confirm reindex")
    sub.add_parser("stats", help="Show index statistics")
    sub.add_parser("verify", help="Verify the policy index (non-zero on failure)")
    search = sub.add_parser("search", help="Search policies")
    search.add_argument("query")
    search.add_argument("--topic", default=None)
    search.add_argument(
        "--mode", default="hybrid", choices=["lexical", "semantic", "hybrid"]
    )
    show = sub.add_parser("show-citation", help="Show a chunk by citation id")
    show.add_argument("citation_id")
    sub.add_parser("eval", help="Run the retrieval evaluation (enforces hard gates)")
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
            if args.command == "search":
                return await _run_search(args.query, args.topic, args.mode)
            if args.command == "show-citation":
                return await _run_show_citation(args.citation_id)
            if args.command == "eval":
                return await _run_eval()
            return 2
        finally:
            await dispose_engine()

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
