"""Evaluation metric + runner tests."""

from __future__ import annotations

from datetime import UTC, datetime

from app.retrieval.embeddings import DeterministicHashEmbedding
from app.retrieval.evaluation import (
    CaseOutcome,
    EvalCase,
    ModeMetrics,
    _compute,
    evaluate,
    hard_gate_failures,
    load_dataset,
)
from app.retrieval.ingestion import ingest
from app.rules.clock import FixedClock
from app.seeds.runner import seed
from sqlalchemy.ext.asyncio import AsyncSession

CLOCK = FixedClock(datetime(2026, 7, 16, 12, 0, tzinfo=UTC))


def _case(topics: list[str], category: str = "direct") -> EvalCase:
    return EvalCase(
        case_id="X",
        query="q",
        topic=None,
        expected_topics=topics,
        expected_support="supported",
        expected_conflict="none",
        source_scope="official",
        include_historical=False,
        category=category,
    )


def _outcome(case: EvalCase, topics: list[str], **kw: object) -> CaseOutcome:
    base: dict[str, object] = {
        "case": case,
        "evidence_topics": topics,
        "support": "supported",
        "conflict": "none",
        "latency_ms": 1,
        "all_active": True,
        "hostile_present": False,
    }
    base.update(kw)
    return CaseOutcome(**base)  # type: ignore[arg-type]


def test_recall_and_mrr() -> None:
    c = _case(["returns"])
    outcomes = [
        _outcome(c, ["returns"]),  # rank 1
        _outcome(c, ["refunds", "returns"]),  # rank 2
        _outcome(c, ["refunds"]),  # miss
    ]
    m = _compute("hybrid", outcomes)
    assert m.recall_at_1 == 1 / 3
    assert m.recall_at_3 == 2 / 3
    assert round(m.mrr, 3) == round((1 + 0.5) / 3, 3)


def test_hard_gate_detection() -> None:
    m = ModeMetrics(mode="hybrid", conflict_detection=0.5)
    assert "conflict_detection" in hard_gate_failures(m)
    ok = ModeMetrics(mode="hybrid")
    assert hard_gate_failures(ok) == []


def test_hostile_present_fails_exclusion_gate() -> None:
    c = _case([], category="hostile")
    m = _compute("hybrid", [_outcome(c, ["fixture_hostile"], hostile_present=True)])
    assert m.hostile_exclusion == 0.0


def test_dataset_loads_and_has_enough_cases() -> None:
    cases = load_dataset()
    assert len(cases) >= 60
    assert any(c.category == "conflict" for c in cases)
    assert any(c.category == "hostile" for c in cases)
    assert any(c.category == "unsupported" for c in cases)


async def test_evaluation_deterministic_and_gates(db_session: AsyncSession) -> None:
    await seed(db_session)
    await ingest(db_session, DeterministicHashEmbedding())
    provider = DeterministicHashEmbedding()
    first = await evaluate(db_session, provider, clock=CLOCK)
    second = await evaluate(db_session, provider, clock=CLOCK)
    for mode in ("lexical", "semantic", "hybrid"):
        assert first[mode].recall_at_1 == second[mode].recall_at_1
        assert first[mode].mrr == second[mode].mrr
    # Hard gates must pass on hybrid.
    assert hard_gate_failures(first["hybrid"]) == []
    assert first["hybrid"].active_version_accuracy == 1.0
    assert first["hybrid"].conflict_detection == 1.0
    assert first["hybrid"].hostile_exclusion == 1.0
