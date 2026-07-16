"""Offline retrieval evaluation: deterministic metrics + hard safety gates.

Runs the versioned dataset through the retrieval service in lexical / semantic / hybrid
modes with the deterministic embedding provider (no LLM). Reports recall@k, MRR, topic
accuracy and safety metrics per mode, and enforces three hard gates on the primary
(hybrid) mode: active-version accuracy, conflict detection and hostile-source exclusion
must all be 1.00.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.paths import get_data_dir
from app.models.enums import PolicySourceType
from app.retrieval.embeddings import EmbeddingProvider
from app.retrieval.models import (
    PolicyRetrievalRequest,
    PolicyRetrievalResult,
    RetrievalMode,
)
from app.retrieval.service import PolicyRetrievalService
from app.rules.clock import Clock, seed_reference_clock

HOSTILE_TOPIC = "fixture_hostile"
HARD_GATES = ("active_version_accuracy", "conflict_detection", "hostile_exclusion")


class EvalCase(BaseModel):
    case_id: str
    query: str
    topic: str | None
    expected_topics: list[str]
    expected_support: str
    expected_conflict: str
    source_scope: str
    include_historical: bool
    category: str


def dataset_path() -> Path:
    return (
        get_data_dir().parent / "evaluations" / "datasets" / "policy_retrieval_v1.json"
    )


def load_dataset(path: Path | None = None) -> list[EvalCase]:
    raw = json.loads((path or dataset_path()).read_text(encoding="utf-8"))
    return [EvalCase.model_validate(c) for c in raw["cases"]]


def _scope_source_types(scope: str) -> frozenset[PolicySourceType] | None:
    if scope == "conflict":
        return frozenset(
            {PolicySourceType.official_policy, PolicySourceType.test_conflict}
        )
    return None  # official default (also hostile_probe and historical)


@dataclass(slots=True)
class CaseOutcome:
    case: EvalCase
    evidence_topics: list[str]
    support: str
    conflict: str
    latency_ms: int
    all_active: bool
    hostile_present: bool


@dataclass(slots=True)
class ModeMetrics:
    mode: str
    recall_at_1: float = 0.0
    recall_at_3: float = 0.0
    recall_at_5: float = 0.0
    mrr: float = 0.0
    topic_accuracy: float = 0.0
    active_version_accuracy: float = 1.0
    conflict_detection: float = 1.0
    hostile_exclusion: float = 1.0
    unsupported_rejection: float = 1.0
    no_active_policy_accuracy: float = 1.0
    historical_correctness: float = 1.0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    outcomes: list[CaseOutcome] = field(default_factory=list)


async def _run_case(
    service: PolicyRetrievalService, case: EvalCase, mode: RetrievalMode, clock: Clock
) -> CaseOutcome:
    start = time.perf_counter()
    result: PolicyRetrievalResult = await service.retrieve(
        PolicyRetrievalRequest(
            query=case.query,
            topic=case.topic,
            mode=mode,
            include_historical=case.include_historical,
        ),
        clock=clock,
        source_types=_scope_source_types(case.source_scope),
    )
    latency_ms = int((time.perf_counter() - start) * 1000)
    topics = [e.topic for e in result.evidence]
    all_active = all(e.status.value == "active" for e in result.evidence)
    return CaseOutcome(
        case=case,
        evidence_topics=topics,
        support=result.support_status.value,
        conflict=result.conflict_status.value,
        latency_ms=latency_ms,
        all_active=all_active,
        hostile_present=HOSTILE_TOPIC in topics,
    )


def _rank_of_expected(topics: list[str], expected: list[str]) -> int | None:
    for rank, topic in enumerate(topics, start=1):
        if topic in expected:
            return rank
    return None


def _compute(mode: str, outcomes: list[CaseOutcome]) -> ModeMetrics:
    m = ModeMetrics(mode=mode, outcomes=outcomes)
    recall_cases = [o for o in outcomes if o.case.expected_topics]
    if recall_cases:
        r1 = r3 = r5 = top1 = 0
        mrr_total = 0.0
        for o in recall_cases:
            rank = _rank_of_expected(o.evidence_topics, o.case.expected_topics)
            if rank is not None:
                mrr_total += 1.0 / rank
                r1 += rank <= 1
                r3 += rank <= 3
                r5 += rank <= 5
            if o.evidence_topics and o.evidence_topics[0] in o.case.expected_topics:
                top1 += 1
        n = len(recall_cases)
        m.recall_at_1, m.recall_at_3, m.recall_at_5 = r1 / n, r3 / n, r5 / n
        m.mrr, m.topic_accuracy = mrr_total / n, top1 / n

    # Active-version accuracy over ordinary official retrieval (not historical).
    official = [
        o for o in outcomes if o.case.source_scope not in ("historical", "conflict")
    ]
    if official:
        m.active_version_accuracy = sum(o.all_active for o in official) / len(official)

    conflict_cases = [o for o in outcomes if o.case.category == "conflict"]
    if conflict_cases:
        m.conflict_detection = sum(
            o.conflict == "conflicting" for o in conflict_cases
        ) / len(conflict_cases)

    m.hostile_exclusion = 1.0 if not any(o.hostile_present for o in outcomes) else 0.0

    # Out-of-domain queries should be rejected. Hostile probes are excluded here: their
    # security property is hostile-source exclusion (a hard gate), not the label; a
    # hostile query mentioning "refund" legitimately retrieves the refund policy.
    reject_cases = [o for o in outcomes if o.case.category == "unsupported"]
    if reject_cases:
        m.unsupported_rejection = sum(
            o.support in ("unsupported", "no_active_policy") for o in reject_cases
        ) / len(reject_cases)

    nap_cases = [o for o in outcomes if o.case.category == "no_active_policy"]
    if nap_cases:
        m.no_active_policy_accuracy = sum(
            o.support == "no_active_policy" for o in nap_cases
        ) / len(nap_cases)

    hist_cases = [o for o in outcomes if o.case.category == "historical"]
    if hist_cases:
        m.historical_correctness = sum(
            bool(set(o.evidence_topics) & set(o.case.expected_topics))
            for o in hist_cases
        ) / len(hist_cases)

    latencies = sorted(o.latency_ms for o in outcomes)
    if latencies:
        m.avg_latency_ms = sum(latencies) / len(latencies)
        m.p95_latency_ms = latencies[
            min(len(latencies) - 1, int(0.95 * len(latencies)))
        ]
    return m


async def evaluate(
    session: AsyncSession,
    provider: EmbeddingProvider,
    *,
    clock: Clock | None = None,
    modes: tuple[RetrievalMode, ...] = (
        RetrievalMode.lexical,
        RetrievalMode.semantic,
        RetrievalMode.hybrid,
    ),
) -> dict[str, ModeMetrics]:
    clock = clock or seed_reference_clock()
    service = PolicyRetrievalService(session, provider)
    cases = load_dataset()
    metrics: dict[str, ModeMetrics] = {}
    for mode in modes:
        outcomes = [await _run_case(service, case, mode, clock) for case in cases]
        metrics[mode.value] = _compute(mode.value, outcomes)
    return metrics


def hard_gate_failures(metrics: ModeMetrics) -> list[str]:
    return [gate for gate in HARD_GATES if getattr(metrics, gate) < 1.0]
