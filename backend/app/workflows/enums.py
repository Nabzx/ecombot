"""Workflow state, status and related enums (S5).

The state machine is explicit: states are a closed vocabulary, and the active / paused /
terminal partition is what the runner uses to decide whether to keep processing. No
consequential-execution states exist yet (that is S6).
"""

from __future__ import annotations

from enum import StrEnum


class WorkflowState(StrEnum):
    """Every state a support-ticket workflow run can occupy."""

    # --- active (currently processing) ---
    RECEIVED = "received"
    VALIDATING = "validating"
    SANITISING = "sanitising"
    CLASSIFYING = "classifying"
    EXTRACTING_IDENTIFIERS = "extracting_identifiers"
    RESOLVING_CUSTOMER = "resolving_customer"
    RESOLVING_ORDER = "resolving_order"
    RETRIEVING_ORDER_DATA = "retrieving_order_data"
    RETRIEVING_POLICY = "retrieving_policy"
    EVALUATING_RULES = "evaluating_rules"
    SUMMARISING_EVIDENCE = "summarising_evidence"
    DRAFTING_RESPONSE = "drafting_response"
    CALCULATING_ROUTE = "calculating_route"

    # --- paused (waiting for a future human/external event) ---
    AWAITING_AGENT = "awaiting_agent"
    AWAITING_APPROVAL = "awaiting_approval"
    NEEDS_INFORMATION = "needs_information"
    ESCALATED = "escalated"

    # --- terminal (cannot continue automatically) ---
    BLOCKED = "blocked"
    FAILED_VALIDATION = "failed_validation"
    FAILED_DEPENDENCY = "failed_dependency"
    FAILED_MODEL = "failed_model"
    CANCELLED = "cancelled"
    RESOLVED_WITHOUT_ACTION = "resolved_without_action"

    # --- S6 approval/execution states (support-ticket-v2 only) ---
    APPROVED_PENDING_EXECUTION = "approved_pending_execution"  # active
    EXECUTING_ACTION = "executing_action"  # active
    APPROVAL_EXPIRED = "approval_expired"  # paused
    ACTION_FAILED = "action_failed"  # paused
    MANUAL_ACTION_REQUIRED = "manual_action_required"  # paused
    APPROVAL_REJECTED = "approval_rejected"  # terminal (completed)
    ACTION_SUCCEEDED = "action_succeeded"  # terminal (completed)


ACTIVE_STATES: frozenset[WorkflowState] = frozenset(
    {
        WorkflowState.RECEIVED,
        WorkflowState.VALIDATING,
        WorkflowState.SANITISING,
        WorkflowState.CLASSIFYING,
        WorkflowState.EXTRACTING_IDENTIFIERS,
        WorkflowState.RESOLVING_CUSTOMER,
        WorkflowState.RESOLVING_ORDER,
        WorkflowState.RETRIEVING_ORDER_DATA,
        WorkflowState.RETRIEVING_POLICY,
        WorkflowState.EVALUATING_RULES,
        WorkflowState.SUMMARISING_EVIDENCE,
        WorkflowState.DRAFTING_RESPONSE,
        WorkflowState.CALCULATING_ROUTE,
        WorkflowState.APPROVED_PENDING_EXECUTION,
        WorkflowState.EXECUTING_ACTION,
    }
)

PAUSED_STATES: frozenset[WorkflowState] = frozenset(
    {
        WorkflowState.AWAITING_AGENT,
        WorkflowState.AWAITING_APPROVAL,
        WorkflowState.NEEDS_INFORMATION,
        WorkflowState.ESCALATED,
        WorkflowState.APPROVAL_EXPIRED,
        WorkflowState.ACTION_FAILED,
        WorkflowState.MANUAL_ACTION_REQUIRED,
    }
)

TERMINAL_STATES: frozenset[WorkflowState] = frozenset(
    {
        WorkflowState.BLOCKED,
        WorkflowState.FAILED_VALIDATION,
        WorkflowState.FAILED_DEPENDENCY,
        WorkflowState.FAILED_MODEL,
        WorkflowState.CANCELLED,
        WorkflowState.RESOLVED_WITHOUT_ACTION,
        WorkflowState.APPROVAL_REJECTED,
        WorkflowState.ACTION_SUCCEEDED,
    }
)

# Terminal states that represent a *successful* completion rather than a failure.
COMPLETED_TERMINAL_STATES: frozenset[WorkflowState] = frozenset(
    {
        WorkflowState.RESOLVED_WITHOUT_ACTION,
        WorkflowState.APPROVAL_REJECTED,
        WorkflowState.ACTION_SUCCEEDED,
    }
)


def is_active(state: WorkflowState) -> bool:
    return state in ACTIVE_STATES


def is_paused(state: WorkflowState) -> bool:
    return state in PAUSED_STATES


def is_terminal(state: WorkflowState) -> bool:
    return state in TERMINAL_STATES


class WorkflowStatus(StrEnum):
    """High-level run status, distinct from the fine-grained state."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def status_for_state(state: WorkflowState) -> WorkflowStatus:
    """The canonical status implied by a state (state and status never disagree)."""
    if state in PAUSED_STATES:
        return WorkflowStatus.PAUSED
    if state == WorkflowState.CANCELLED:
        return WorkflowStatus.CANCELLED
    if state in COMPLETED_TERMINAL_STATES:
        return WorkflowStatus.COMPLETED
    if state in TERMINAL_STATES:
        return WorkflowStatus.FAILED
    if state == WorkflowState.RECEIVED:
        return WorkflowStatus.PENDING
    return WorkflowStatus.RUNNING


class TriggerType(StrEnum):
    """Why a workflow run was created."""

    TICKET_RECEIVED = "ticket_received"
    MANUAL_REPROCESS = "manual_reprocess"
    EVALUATION = "evaluation"
    REPLAY = "replay"


class StepStatus(StrEnum):
    """Status of a single workflow step attempt."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowFailureCode(StrEnum):
    """Stable typed failure codes recorded on runs and steps."""

    VALIDATION_FAILED = "validation_failed"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    MODEL_FAILED = "model_failed"
    OWNERSHIP_BLOCKED = "ownership_blocked"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    STEP_LIMIT_EXCEEDED = "step_limit_exceeded"
    CHECKPOINT_INVALID = "checkpoint_invalid"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


class ProposedActionStatus(StrEnum):
    """Lifecycle of a proposed action, including S6 approval and execution.

    Convention: a Supervisor *rejection* moves the proposal to ``REJECTED``;
    ``SUPERSEDED`` is reserved for a proposal replaced by a newer one on the same run;
    ``CANCELLED`` is a withdrawn (agent-cancelled) request. Successful execution reaches
    ``COMPLETED``; a failed technical execution never marks the proposal completed.
    """

    DRAFT = "draft"
    READY_FOR_AGENT = "ready_for_agent"
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED = "blocked"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"
    # --- S6 approval/execution ---
    APPROVED_PENDING_EXECUTION = "approved_pending_execution"
    COMPLETED = "completed"
    REJECTED = "rejected"


# Legal proposed-action transitions. Anything not listed is rejected.
PROPOSED_ACTION_TRANSITIONS: dict[
    ProposedActionStatus, frozenset[ProposedActionStatus]
] = {
    ProposedActionStatus.DRAFT: frozenset(
        {
            ProposedActionStatus.READY_FOR_AGENT,
            ProposedActionStatus.AWAITING_APPROVAL,
            ProposedActionStatus.BLOCKED,
            ProposedActionStatus.SUPERSEDED,
            ProposedActionStatus.CANCELLED,
        }
    ),
    ProposedActionStatus.READY_FOR_AGENT: frozenset(
        {
            ProposedActionStatus.AWAITING_APPROVAL,
            ProposedActionStatus.SUPERSEDED,
            ProposedActionStatus.CANCELLED,
        }
    ),
    ProposedActionStatus.AWAITING_APPROVAL: frozenset(
        {
            ProposedActionStatus.APPROVED_PENDING_EXECUTION,
            ProposedActionStatus.REJECTED,
            ProposedActionStatus.SUPERSEDED,
            ProposedActionStatus.CANCELLED,
        }
    ),
    ProposedActionStatus.APPROVED_PENDING_EXECUTION: frozenset(
        {
            ProposedActionStatus.COMPLETED,
            # A failed/dead-lettered execution leaves the proposal awaiting manual
            # handling; a Supervisor retry returns it here.
            ProposedActionStatus.APPROVED_PENDING_EXECUTION,
        }
    ),
}

# Terminal proposed-action statuses.
TERMINAL_PROPOSED_ACTION_STATUSES: frozenset[ProposedActionStatus] = frozenset(
    {
        ProposedActionStatus.BLOCKED,
        ProposedActionStatus.SUPERSEDED,
        ProposedActionStatus.CANCELLED,
        ProposedActionStatus.COMPLETED,
        ProposedActionStatus.REJECTED,
    }
)


def is_valid_proposed_action_transition(
    source: ProposedActionStatus, destination: ProposedActionStatus
) -> bool:
    return destination in PROPOSED_ACTION_TRANSITIONS.get(source, frozenset())


class ReplayMode(StrEnum):
    """How a replay reproduces a run."""

    RECORDED_OUTPUTS = "recorded_outputs"
    DETERMINISTIC_MOCK = "deterministic_mock"
    CURRENT_CONFIGURATION = "current_configuration"
