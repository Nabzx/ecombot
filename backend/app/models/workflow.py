"""S5 workflow persistence: runs, checkpoints, steps, tool calls and proposed actions.

Durable, resumable and replayable state for the support-ticket workflow. Stores only
redacted, serialisable snapshots and IDs — never ORM graphs, secrets or hidden
reasoning. No approval-decision, outbox or execution columns exist (that is S6).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import pg_enum
from app.workflows.enums import (
    ProposedActionStatus,
    StepStatus,
    TriggerType,
    WorkflowFailureCode,
    WorkflowState,
    WorkflowStatus,
)


class WorkflowRun(UUIDPKMixin, TimestampMixin, Base):
    """One execution of a versioned workflow definition over a ticket."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("correlation_id", name="uq_workflow_runs_correlation"),
        Index("ix_workflow_runs_state", "current_state"),
        Index("ix_workflow_runs_status", "status"),
        Index("ix_workflow_runs_ticket", "ticket_id"),
        Index("ix_workflow_runs_created_at", "created_at"),
        # At most one non-terminal live run per ticket + workflow version. Replay runs
        # are exempt (they intentionally re-run an existing ticket for comparison).
        Index(
            "uq_workflow_runs_active_ticket",
            "ticket_id",
            "workflow_name",
            "workflow_version",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'running', 'paused') "
                "AND trigger_type <> 'replay'"
            ),
        ),
    )

    workflow_name: Mapped[str] = mapped_column(String(60), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(20), nullable=False)
    state_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[WorkflowStatus] = mapped_column(
        pg_enum(WorkflowStatus, "workflow_status"), nullable=False
    )
    current_state: Mapped[WorkflowState] = mapped_column(
        pg_enum(WorkflowState, "workflow_state"), nullable=False
    )
    current_step: Mapped[str] = mapped_column(String(60), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_type: Mapped[TriggerType] = mapped_column(
        pg_enum(TriggerType, "workflow_trigger_type"), nullable=False
    )
    initiated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Soft reference to the latest checkpoint (no DB FK to avoid a circular dependency).
    last_checkpoint_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resume_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replay_source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    failure_code: Mapped[WorkflowFailureCode | None] = mapped_column(
        pg_enum(WorkflowFailureCode, "workflow_failure_code"), nullable=True
    )
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    # --- concurrency control (claim + lease + optimistic version) ---
    claimed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<WorkflowRun {self.workflow_name} {self.current_state} {self.status}>"


class WorkflowCheckpoint(UUIDPKMixin, Base):
    """An immutable, hashed snapshot of workflow state at a step boundary."""

    __tablename__ = "workflow_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id", "step_index", name="uq_workflow_checkpoints_run_step"
        ),
        Index("ix_workflow_checkpoints_run", "workflow_run_id"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[WorkflowState] = mapped_column(
        pg_enum(WorkflowState, "workflow_state"), nullable=False
    )
    state_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    snapshot_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<WorkflowCheckpoint run={self.workflow_run_id} step={self.step_index}>"


class WorkflowStep(UUIDPKMixin, Base):
    """A single executed step attempt within a run."""

    __tablename__ = "workflow_steps"
    __table_args__ = (
        Index("ix_workflow_steps_run", "workflow_run_id"),
        Index("ix_workflow_steps_run_index", "workflow_run_id", "step_index"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(60), nullable=False)
    source_state: Mapped[WorkflowState] = mapped_column(
        pg_enum(WorkflowState, "workflow_state"), nullable=False
    )
    destination_state: Mapped[WorkflowState | None] = mapped_column(
        pg_enum(WorkflowState, "workflow_state"), nullable=True
    )
    status: Mapped[StepStatus] = mapped_column(
        pg_enum(StepStatus, "workflow_step_status"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_summary_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    output_summary_json: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model_call_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    tool_call_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    citation_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<WorkflowStep {self.step_name} {self.status}>"


class WorkflowToolCall(UUIDPKMixin, Base):
    """A persisted record of a read-only tool call made during a workflow step."""

    __tablename__ = "workflow_tool_calls"
    __table_args__ = (
        Index("ix_workflow_tool_calls_run", "workflow_run_id"),
        Index("ix_workflow_tool_calls_step", "workflow_step_id"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_step_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    output_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<WorkflowToolCall {self.tool_name} {self.status}>"


class ProposedAction(UUIDPKMixin, TimestampMixin, Base):
    """A proposal produced before any S6 approval. Never approved or executed here."""

    __tablename__ = "proposed_actions"
    __table_args__ = (
        Index("ix_proposed_actions_run", "workflow_run_id"),
        Index("ix_proposed_actions_ticket", "ticket_id"),
        Index("ix_proposed_actions_status", "status"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[ProposedActionStatus] = mapped_column(
        pg_enum(ProposedActionStatus, "proposed_action_status"), nullable=False
    )
    risk_level: Mapped[str] = mapped_column(String(24), nullable=False)
    required_role: Mapped[str | None] = mapped_column(String(24), nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    amount_pence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    draft_response_subject: Mapped[str] = mapped_column(Text, nullable=False)
    draft_response_body: Mapped[str] = mapped_column(Text, nullable=False)
    citation_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    rule_result_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    decision_summary_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ProposedAction {self.action_type} {self.status}>"
