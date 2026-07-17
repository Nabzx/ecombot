"""Typed workflow execution context and step-result types (no global mutable state)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.service import ModelService
from app.rules.clock import Clock, SystemClock
from app.workflows.enums import WorkflowState


@dataclass
class WorkflowLimits:
    """Bounds applied while running a workflow."""

    max_steps_per_invocation: int = 20
    total_deadline_seconds: float = 120.0
    step_timeout_seconds: float = 30.0
    max_evidence_citations: int = 8
    lease_seconds: int = 60


@dataclass
class WorkflowExecutionContext:
    """Everything a step handler needs, injected explicitly."""

    session: AsyncSession
    correlation_id: str
    worker_id: str
    clock: Clock = field(default_factory=SystemClock)
    model_service: ModelService = field(default_factory=ModelService)
    provider_preference: str | None = None
    limits: WorkflowLimits = field(default_factory=WorkflowLimits)
    # Force a deterministic mock scenario during model steps (evaluation/replay only).
    mock_scenario: str = ""
    # Set by the runner before each step so model/tool calls link to the workflow.
    workflow_run_id: uuid.UUID | None = None
    current_step_id: uuid.UUID | None = None


@dataclass
class StepExecutionResult:
    """The typed outcome of one step handler."""

    destination_state: WorkflowState
    state_fragment: dict[str, object] = field(default_factory=dict)
    model_call_ids: list[str] = field(default_factory=list)
    tool_call_ids: list[str] = field(default_factory=list)
    citation_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    retryable: bool = False
    failure_code: str | None = None
    error_message: str | None = None
    checkpoint_required: bool = True
