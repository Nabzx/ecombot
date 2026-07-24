"""Offline observability / audit / reliability evaluation (S7).

Deterministic and network-free. Drives real approval → execution journeys and checks
every consequential action is audited, the hash-chain detects tampering, logs are
PII-safe, traces are complete, correlation is preserved across stages, and no S6 safety
gate regressed. Every hard gate counts unsafe outcomes and must be 0.

Run: ``python -m app.audit.evaluation``.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions.evaluation import (
    _approved_refund_job,
    _reset,
)
from app.actions.evaluation import (
    run_evaluation as run_s6_evaluation,
)
from app.audit.enums import AuditEventType
from app.audit.repository import AuditRepository
from app.core.logging import ContextFilter, JsonFormatter, RedactionFilter
from app.core.paths import get_data_dir
from app.outbox.processor import OutboxProcessor, ProcessOutcome
from app.rules.clock import Clock, seed_reference_clock
from app.tracing.exporters import CollectingExporter
from app.tracing.spans import Tracer

HARD_GATES = (
    "consequential_action_without_audit",
    "broken_chain_accepted",
    "pii_in_logs",
    "orphan_span",
    "correlation_lost",
    "s6_regression",
)


def _evaluations_dir() -> Path:
    return get_data_dir().parent / "evaluations"


def default_dataset_path() -> Path:
    return _evaluations_dir() / "datasets" / "observability_v1.json"


def report_dir() -> Path:
    return _evaluations_dir() / "reports" / "observability"


@dataclass
class Evaluation:
    dataset_version: str = ""
    case_count: int = 0
    scenarios_run: int = 0
    scenarios_passed: int = 0
    category_coverage: dict[str, int] = field(default_factory=dict)
    gates: dict[str, int] = field(default_factory=lambda: dict.fromkeys(HARD_GATES, 0))
    failures: list[str] = field(default_factory=list)

    @property
    def all_gates_pass(self) -> bool:
        return all(v == 0 for v in self.gates.values())


def _clock() -> Clock:
    return seed_reference_clock()


async def _count(factory: async_sessionmaker[AsyncSession], table: str) -> int:
    async with factory() as session:
        value = await session.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        return int(value or 0)


async def _reset_all(factory: async_sessionmaker[AsyncSession]) -> None:
    """Reset the S6 execution tables *and* the audit log, isolating each scenario."""
    await _reset(factory)
    async with factory() as session:
        await session.execute(text("TRUNCATE TABLE audit_events RESTART IDENTITY"))
        await session.commit()


# --- scenarios ----------------------------------------------------------------------
async def _scn_actions_are_audited(f: async_sessionmaker[AsyncSession]) -> bool:
    job_id, _, _ = await _approved_refund_job(f)
    await OutboxProcessor(f, clock=_clock()).process_job(job_id)
    async with f() as s:
        rows = await AuditRepository(s).list_events(limit=200)
        types = {r.event_type for r in rows}
        executed = await _count(f, "executed_actions")
        n_exec_events = sum(
            1 for r in rows if r.event_type == AuditEventType.ACTION_EXECUTED.value
        )
    # Every executed action has an audit record, and the key decisions are all present.
    return (
        AuditEventType.APPROVAL_APPROVED.value in types
        and AuditEventType.OUTBOX_JOB_CREATED.value in types
        and AuditEventType.ACTION_EXECUTED.value in types
        and n_exec_events == executed
    )


async def _scn_chain_detects_tamper(f: async_sessionmaker[AsyncSession]) -> bool:
    job_id, _, _ = await _approved_refund_job(f)
    await OutboxProcessor(f, clock=_clock()).process_job(job_id)
    async with f() as s:
        if not (await AuditRepository(s).verify_chain()).ok:
            return False
        await s.execute(
            text("UPDATE audit_events SET summary = 'tampered' WHERE sequence = 1")
        )
        await s.commit()
        broken = await AuditRepository(s).verify_chain()
    return not broken.ok  # tampering must always be detected


async def _scn_logs_are_pii_safe(f: async_sessionmaker[AsyncSession]) -> bool:
    # Mirror the production log pipeline (context + redaction filters), emit adversarial
    # PII/secret content, and assert the rendered output leaks nothing.
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(self.format(record))

    handler = _Capture()
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("agentops.eval.pii")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info(
            "processing jane.doe@example.com 07911123456 "
            "card 4111111111111111 password=hunter2 Bearer sk-secret"
        )
    finally:
        logger.removeHandler(handler)
    blob = "\n".join(captured)
    leaked = any(
        token in blob
        for token in (
            "jane.doe@example.com",
            "07911123456",
            "4111111111111111",
            "hunter2",
            "sk-secret",
        )
    )
    return not leaked


async def _scn_trace_is_complete(f: async_sessionmaker[AsyncSession]) -> bool:
    exporter = CollectingExporter()
    job_id, _, _ = await _approved_refund_job(f)
    result = await OutboxProcessor(
        f,
        clock=_clock(),
        tracer=Tracer(exporter, clock=_clock()),
    ).process_job(job_id)
    return (
        result.outcome == ProcessOutcome.SUCCEEDED
        and exporter.orphan_spans() == []
        and len({s.trace_id for s in exporter.spans}) == 1
    )


async def _scn_correlation_preserved(f: async_sessionmaker[AsyncSession]) -> bool:
    job_id, _, _ = await _approved_refund_job(f)
    await OutboxProcessor(f, clock=_clock()).process_job(job_id)
    async with f() as s:
        key = await s.scalar(
            text("SELECT idempotency_key FROM outbox_jobs WHERE id = :j"),
            {"j": str(job_id)},
        )
        chain = await AuditRepository(s).list_for_correlation(str(key))
        types = {r.event_type for r in chain}
    # Approval and execution audit events share the one correlation id.
    return (
        AuditEventType.APPROVAL_APPROVED.value in types
        and AuditEventType.ACTION_EXECUTED.value in types
    )


async def _scn_s6_not_regressed(f: async_sessionmaker[AsyncSession]) -> bool:
    await _reset(f)
    result = await run_s6_evaluation(write_report=False, session_factory=f)
    return result.all_gates_pass


_SCENARIOS = {
    "actions_audited": (_scn_actions_are_audited, "consequential_action_without_audit"),
    "chain_tamper": (_scn_chain_detects_tamper, "broken_chain_accepted"),
    "logs_pii_safe": (_scn_logs_are_pii_safe, "pii_in_logs"),
    "trace_complete": (_scn_trace_is_complete, "orphan_span"),
    "correlation": (_scn_correlation_preserved, "correlation_lost"),
    "s6_regression": (_scn_s6_not_regressed, "s6_regression"),
}


async def run_evaluation(
    *,
    dataset_path: Path | None = None,
    write_report: bool = True,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> Evaluation:
    path = dataset_path or default_dataset_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    ev = Evaluation(
        dataset_version=data["dataset_version"], case_count=data["case_count"]
    )
    ev.category_coverage = dict(Counter(c["category"] for c in data["cases"]))

    from app.db.session import get_sessionmaker

    factory = session_factory or get_sessionmaker()
    for name, (scenario, gate) in _SCENARIOS.items():
        await _reset_all(factory)
        ev.scenarios_run += 1
        try:
            passed = await scenario(factory)
        except Exception as exc:  # pragma: no cover - surfaced as a failure
            passed = False
            ev.failures.append(f"{name}: {type(exc).__name__}: {exc}")
        if passed:
            ev.scenarios_passed += 1
        else:
            ev.gates[gate] += 1
            ev.failures.append(f"scenario failed: {name} (gate {gate})")
    await _reset_all(factory)

    if write_report:
        _write_report(ev)
    return ev


def _write_report(ev: Evaluation) -> None:
    directory = report_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"observability_{stamp}.json"
    payload = {
        "dataset_version": ev.dataset_version,
        "case_count": ev.case_count,
        "category_coverage": ev.category_coverage,
        "scenarios_run": ev.scenarios_run,
        "scenarios_passed": ev.scenarios_passed,
        "hard_gates": ev.gates,
        "all_gates_pass": ev.all_gates_pass,
        "failures": ev.failures,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    import asyncio

    ev = asyncio.run(run_evaluation())
    print(f"dataset           {ev.dataset_version}")
    print(f"cases             {ev.case_count}")
    print(f"scenarios         {ev.scenarios_passed}/{ev.scenarios_run} passed")
    print(f"coverage          {ev.category_coverage}")
    print("hard gates (must be 0):")
    for gate in HARD_GATES:
        print(f"  {gate:40} {ev.gates[gate]}")
    for failure in ev.failures:
        print(f"  ! {failure}")
    if ev.all_gates_pass:
        print("ALL HARD GATES PASS")
        return 0
    print("HARD GATE FAILURE")
    return 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
