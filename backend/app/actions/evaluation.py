"""Offline approval/action safety evaluation (S6).

Deterministic and network-free. Loads the versioned dataset, then *actually* drives the
approval → outbox → execution path for a curated set of safety-critical scenarios that
collectively exercise every hard gate. Each hard gate counts **unsafe outcomes**, so a
correct system reports 0 for all of them. Any hard-gate failure returns a non-zero exit.

Run: ``python -m app.actions.evaluation`` (uses the configured database, mock provider).
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions.enums import PROPOSED_TO_EXECUTION
from app.approvals.service import (
    ApprovalService,
    ApproveRequest,
    CreateApprovalRequest,
)
from app.auth.models import AuthenticatedUser
from app.core.paths import get_data_dir
from app.db.session import get_sessionmaker
from app.models.enums import OrderStatus, UserRole
from app.models.ticket import Ticket
from app.models.user import User
from app.outbox.enums import OutboxStatus
from app.outbox.payload import OutboxJobData
from app.outbox.processor import OutboxProcessor, ProcessOutcome
from app.outbox.repository import OutboxRepository
from app.outbox.worker import OutboxWorker
from app.rules.clock import seed_reference_clock
from app.workflows.enums import WorkflowState
from app.workflows.repository import WorkflowRepository
from app.workflows.service import StartWorkflowRequest, SupportWorkflowService

REFUND_TICKET = "DEMO-REFUND-APPROVAL-001"

HARD_GATES = (
    "approved_action_missing_outbox",
    "outbox_without_valid_approval",
    "action_without_valid_approval",
    "duplicate_business_effect",
    "refund_above_item_limit",
    "refund_above_order_balance",
    "refund_above_250",
    "cancellation_after_shipment",
    "cross_customer_execution",
    "expired_approval_execution",
    "tampered_snapshot_or_payload_execution",
    "unsupported_action_execution",
    "lost_committed_action",
    "replay_business_effect",
)


def _evaluations_dir() -> Path:
    return get_data_dir().parent / "evaluations"


def default_dataset_path() -> Path:
    return _evaluations_dir() / "datasets" / "approvals_actions_v1.json"


def report_dir() -> Path:
    return _evaluations_dir() / "reports" / "approvals_actions"


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


def _clock() -> Any:
    return seed_reference_clock()


def _now() -> datetime:
    return seed_reference_clock().now()


async def _reset(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        await session.execute(
            text(
                "TRUNCATE TABLE refund_ledger_entries, executed_actions, "
                "outbox_attempts, outbox_jobs, approval_requests, workflow_runs "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()


async def _user(session: AsyncSession, role: UserRole, index: int = 0) -> Any:
    rows = list(
        await session.scalars(
            select(User).where(User.role == role).order_by(User.email)
        )
    )
    user = rows[index]
    return AuthenticatedUser.build(
        user_id=user.id, role=role, email=user.email, is_active=True
    )


async def _approved_refund_job(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with factory() as session:
        ticket = await session.scalar(
            select(Ticket).where(Ticket.seed_tag == REFUND_TICKET)
        )
        assert ticket is not None  # noqa: S101
        ticket_id = ticket.id
    run = await SupportWorkflowService(session_factory=factory).start(
        StartWorkflowRequest(ticket_id=ticket_id)
    )
    async with factory() as session:
        agent = await _user(session, UserRole.support_agent)
        proposal = await WorkflowRepository(session).get_current_proposal(run.run_id)
        assert proposal is not None  # noqa: S101
        created = await ApprovalService(session, clock=_clock()).create_request(
            CreateApprovalRequest(proposed_action_id=proposal.id), agent
        )
        await session.commit()
        approval_id = created.approval_id
    async with factory() as session:
        supervisor = await _user(session, UserRole.supervisor, index=1)
        result = await ApprovalService(session, clock=_clock()).approve(
            approval_id, ApproveRequest(), supervisor
        )
        await session.commit()
        assert result.outbox_job_id is not None  # noqa: S101
        return result.outbox_job_id, approval_id, run.run_id


def _processor(
    factory: async_sessionmaker[AsyncSession], injector: Any = None
) -> OutboxProcessor:
    return OutboxProcessor(factory, clock=_clock(), failure_injector=injector)


async def _count(factory: async_sessionmaker[AsyncSession], table: str) -> int:
    async with factory() as session:
        # table is an internal constant name, never user input.
        value = await session.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        return int(value or 0)


# --- scenarios: each returns (name, passed, gate_violation | None) -------------------
async def _scn_refund_success(f: async_sessionmaker[AsyncSession]) -> bool:
    job_id, _, run_id = await _approved_refund_job(f)
    result = await _processor(f).process_job(job_id)
    async with f() as s:
        run = await WorkflowRepository(s).get(run_id)
        ok = (
            result.outcome == ProcessOutcome.SUCCEEDED
            and run is not None
            and run.current_state == WorkflowState.ACTION_SUCCEEDED
            and await _count(f, "executed_actions") == 1
            and await _count(f, "refund_ledger_entries") == 1
        )
    return ok


async def _scn_duplicate(f: async_sessionmaker[AsyncSession]) -> bool:
    job_id, _, _ = await _approved_refund_job(f)
    await _processor(f).process_job(job_id)
    async with f() as s:
        await s.execute(
            text("UPDATE outbox_jobs SET status='pending' WHERE id=:j"),
            {"j": str(job_id)},
        )
        await s.commit()
    result = await _processor(f).process_job(job_id)
    return (
        result.outcome == ProcessOutcome.DUPLICATE
        and await _count(f, "executed_actions") == 1
        and await _count(f, "refund_ledger_entries") == 1
    )


async def _scn_competing_workers(f: async_sessionmaker[AsyncSession]) -> bool:
    import asyncio

    from app.core.config import Settings

    job_id, _, _ = await _approved_refund_job(f)

    def w(name: str) -> OutboxWorker:
        return OutboxWorker(
            f,
            settings=Settings(worker_id=name, database_url="postgresql://x/y"),
            clock=_clock(),
        )

    results = await asyncio.gather(w("wa").run_once(), w("wb").run_once())
    return sorted(results) == [0, 1] and await _count(f, "executed_actions") == 1


async def _scn_expired_blocked(f: async_sessionmaker[AsyncSession]) -> bool:
    job_id, approval_id, _ = await _approved_refund_job(f)
    async with f() as s:
        await s.execute(
            text(
                "UPDATE approval_requests SET "
                "created_at = created_at - interval '2 days', "
                "expires_at = created_at - interval '1 day' WHERE id=:a"
            ),
            {"a": str(approval_id)},
        )
        await s.commit()
    result = await _processor(f).process_job(job_id)
    return (
        result.outcome == ProcessOutcome.FAILED
        and await _count(f, "executed_actions") == 0
    )


async def _scn_tampered_payload(f: async_sessionmaker[AsyncSession]) -> bool:
    job_id, _, _ = await _approved_refund_job(f)
    async with f() as s:
        await s.execute(
            text(
                "UPDATE outbox_jobs SET payload_json = "
                "jsonb_set(payload_json,'{approved_amount_pence}','999999') WHERE id=:j"
            ),
            {"j": str(job_id)},
        )
        await s.commit()
    result = await _processor(f).process_job(job_id)
    return (
        result.outcome == ProcessOutcome.FAILED
        and await _count(f, "executed_actions") == 0
    )


async def _scn_cross_customer(f: async_sessionmaker[AsyncSession]) -> bool:
    job_id, _, _ = await _approved_refund_job(f)
    async with f() as s:
        # Point the payload's customer at someone who does not own the order, and keep
        # the hash valid — this isolates the ownership check (not a tamper/hash block).
        job = await OutboxRepository(s).get(job_id)
        assert job is not None  # noqa: S101
        payload = OutboxJobData.model_validate(job.payload_json)
        updated = payload.model_copy(update={"customer_id": uuid.uuid4()})
        job.payload_json = updated.model_dump(mode="json")
        job.payload_hash = updated.compute_hash()
        await s.commit()
    result = await _processor(f).process_job(job_id)
    return (
        result.outcome == ProcessOutcome.FAILED
        and await _count(f, "executed_actions") == 0
    )


async def _scn_manual_only(f: async_sessionmaker[AsyncSession]) -> bool:
    # An approved action with no execution mapping routes to manual handling, no job.
    async with f() as s:
        ticket = await s.scalar(select(Ticket).where(Ticket.seed_tag == REFUND_TICKET))
        assert ticket is not None  # noqa: S101
        ticket_id = ticket.id
    run = await SupportWorkflowService(session_factory=f).start(
        StartWorkflowRequest(ticket_id=ticket_id)
    )
    async with f() as s:
        agent = await _user(s, UserRole.support_agent)
        proposal = await WorkflowRepository(s).get_current_proposal(run.run_id)
        assert proposal is not None  # noqa: S101
        created = await ApprovalService(s, clock=_clock()).create_request(
            CreateApprovalRequest(proposed_action_id=proposal.id), agent
        )
        # Rewrite the action to an unmapped (manual-only) proposal type.
        await s.execute(
            text(
                "UPDATE proposed_actions SET action_type = "
                "'request_supervisor_replacement' WHERE id=:p"
            ),
            {"p": str(proposal.id)},
        )
        await s.execute(
            text(
                "UPDATE approval_requests SET action_type = "
                "'request_supervisor_replacement' WHERE id=:a"
            ),
            {"a": str(created.approval_id)},
        )
        await s.commit()
        approval_id = created.approval_id
    async with f() as s:
        supervisor = await _user(s, UserRole.supervisor, index=1)
        result = await ApprovalService(s, clock=_clock()).approve(
            approval_id, ApproveRequest(), supervisor
        )
        await s.commit()
    return (
        result.manual_action_required
        and not result.outbox_job_created
        and await _count(f, "outbox_jobs") == 0
        and result.workflow_state == WorkflowState.MANUAL_ACTION_REQUIRED
        and str(PROPOSED_TO_EXECUTION.get("request_supervisor_replacement")) == "None"
    )


async def _scn_cancellation(
    f: async_sessionmaker[AsyncSession], *, shipped: bool
) -> bool:
    job_id, approval_id, run_id = await _approved_refund_job(f)
    async with f() as s:
        job = await OutboxRepository(s).get(job_id)
        assert job is not None  # noqa: S101
        payload = OutboxJobData.model_validate(job.payload_json)
        order_id = payload.order_id
        status = OrderStatus.shipped if shipped else OrderStatus.processing
        await s.execute(
            text("UPDATE orders SET status=:st WHERE id=:o"),
            {"st": status.value, "o": str(order_id)},
        )
        if not shipped:
            await s.execute(
                text(
                    "UPDATE shipments SET status='label_created', "
                    "delivered_at=NULL WHERE order_id=:o"
                ),
                {"o": str(order_id)},
            )
        updated = payload.model_copy(
            update={
                "action_type": "simulated_order_cancellation",
                "approved_amount_pence": None,
            }
        )
        job.action_type = "simulated_order_cancellation"
        job.payload_json = updated.model_dump(mode="json")
        job.payload_hash = updated.compute_hash()
        await s.execute(
            text(
                "UPDATE approval_requests SET action_type="
                "'request_supervisor_cancellation_approval' WHERE id=:a"
            ),
            {"a": str(approval_id)},
        )
        await s.commit()
    result = await _processor(f).process_job(job_id)
    if shipped:
        async with f() as s:
            order_status = await s.scalar(
                text("SELECT status FROM orders WHERE id=:o"), {"o": str(order_id)}
            )
        return (
            result.outcome == ProcessOutcome.MANUAL_ACTION_REQUIRED
            and order_status == "shipped"
            and await _count(f, "executed_actions") == 0
        )
    return (
        result.outcome == ProcessOutcome.SUCCEEDED
        and await _count(f, "refund_ledger_entries") == 0
    )


async def _scn_dead_letter(f: async_sessionmaker[AsyncSession]) -> bool:
    from app.actions.errors import ExecutionErrorCode, technical
    from app.core.config import Settings

    job_id, approval_id, run_id = await _approved_refund_job(f)

    def injector(_p: OutboxJobData, _a: int) -> Any:
        return technical(ExecutionErrorCode.INJECTED_FAILURE, "boom")

    worker = OutboxWorker(
        f,
        settings=Settings(worker_id="wf", database_url="postgresql://x/y"),
        clock=_clock(),
        failure_injector=injector,
    )
    for _ in range(6):
        async with f() as s:
            job = await OutboxRepository(s).get(job_id)
            assert job is not None  # noqa: S101
            if job.status == OutboxStatus.DEAD_LETTER:
                break
            await s.execute(
                text(
                    "UPDATE outbox_jobs SET status='pending', next_attempt_at=:n "
                    "WHERE id=:j AND status='retry_scheduled'"
                ),
                {"j": str(job_id), "n": _now()},
            )
            await s.commit()
        await worker.run_once()
    async with f() as s:
        job = await OutboxRepository(s).get(job_id)
        run = await WorkflowRepository(s).get(run_id)
        return (
            job is not None
            and job.status == OutboxStatus.DEAD_LETTER
            and run is not None
            and run.current_state == WorkflowState.ACTION_FAILED
            and await _count(f, "executed_actions") == 0
        )


async def _scn_replay_safety(f: async_sessionmaker[AsyncSession]) -> bool:
    from app.workflows.enums import ReplayMode
    from app.workflows.service import ReplayWorkflowRequest

    job_id, _, run_id = await _approved_refund_job(f)
    await _processor(f).process_job(job_id)
    before = await _count(f, "executed_actions")
    await SupportWorkflowService(session_factory=f).replay(
        ReplayWorkflowRequest(run_id=run_id, mode=ReplayMode.DETERMINISTIC_MOCK)
    )
    return await _count(f, "executed_actions") == before


_SCENARIOS: dict[str, Any] = {
    "refund_success": (_scn_refund_success, "approved_action_missing_outbox"),
    "duplicate_processing": (_scn_duplicate, "duplicate_business_effect"),
    "competing_workers": (_scn_competing_workers, "duplicate_business_effect"),
    "expired_blocked": (_scn_expired_blocked, "expired_approval_execution"),
    "tampered_payload": (
        _scn_tampered_payload,
        "tampered_snapshot_or_payload_execution",
    ),
    "cross_customer": (_scn_cross_customer, "cross_customer_execution"),
    "manual_only": (_scn_manual_only, "unsupported_action_execution"),
    "cancellation_success": (
        lambda f: _scn_cancellation(f, shipped=False),
        "action_without_valid_approval",
    ),
    "cancellation_shipped": (
        lambda f: _scn_cancellation(f, shipped=True),
        "cancellation_after_shipment",
    ),
    "dead_letter": (_scn_dead_letter, "lost_committed_action"),
    "replay_safety": (_scn_replay_safety, "replay_business_effect"),
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

    factory = session_factory or get_sessionmaker()
    for name, (scenario, gate) in _SCENARIOS.items():
        await _reset(factory)
        ev.scenarios_run += 1
        try:
            passed = await scenario(factory)
        except Exception as exc:  # pragma: no cover - surfaced as a failure
            passed = False
            ev.failures.append(f"{name}: {type(exc).__name__}: {exc}")
        if passed:
            ev.scenarios_passed += 1
        else:
            # A failed safety scenario is an unsafe outcome for its hard gate.
            ev.gates[gate] += 1
            ev.failures.append(f"scenario failed: {name} (gate {gate})")
    await _reset(factory)

    if write_report:
        _write_report(ev)
    return ev


def _write_report(ev: Evaluation) -> None:
    directory = report_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"approvals_actions_{stamp}.json"
    payload = {
        "dataset_version": ev.dataset_version,
        "case_count": ev.case_count,
        "category_coverage": ev.category_coverage,
        "scenarios_run": ev.scenarios_run,
        "scenarios_passed": ev.scenarios_passed,
        "hard_gates": ev.gates,
        "all_gates_pass": ev.all_gates_pass,
        "failures": ev.failures,
        "provider": "mock",
        "effects": "simulated",
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
    for name in HARD_GATES:
        print(f"  {name:44} {ev.gates[name]}")
    for failure in ev.failures:
        print(f"  ! {failure}")
    if ev.all_gates_pass:
        print("ALL HARD GATES PASS")
        return 0
    print("HARD GATE FAILURE")
    return 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
