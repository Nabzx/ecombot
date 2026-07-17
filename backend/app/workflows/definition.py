"""Workflow identity, versioning and the explicit transition table (S5).

``support-ticket-v1`` is the only workflow definition. The transition table is the one
source of truth for legal edges; branches are chosen by step handlers from typed results
(never arbitrary model text), and every produced destination is validated against it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.workflows.enums import (
    PAUSED_STATES,
    TERMINAL_STATES,
    WorkflowState,
    is_terminal,
)

WORKFLOW_NAME = "support-ticket-v1"
WORKFLOW_VERSION = "1.0.0"
STATE_SCHEMA_VERSION = "workflow-state-v1"

# The handler name that runs in each active source state (produces the next state).
STATE_HANDLERS: dict[WorkflowState, str] = {
    WorkflowState.RECEIVED: "receive",
    WorkflowState.VALIDATING: "validate",
    WorkflowState.SANITISING: "sanitise",
    WorkflowState.CLASSIFYING: "classify",
    WorkflowState.EXTRACTING_IDENTIFIERS: "extract_identifiers",
    WorkflowState.RESOLVING_CUSTOMER: "resolve_customer",
    WorkflowState.RESOLVING_ORDER: "resolve_order",
    WorkflowState.RETRIEVING_ORDER_DATA: "retrieve_order_data",
    WorkflowState.RETRIEVING_POLICY: "retrieve_policy",
    WorkflowState.EVALUATING_RULES: "evaluate_rules",
    WorkflowState.SUMMARISING_EVIDENCE: "summarise_evidence",
    WorkflowState.DRAFTING_RESPONSE: "draft_response",
    WorkflowState.CALCULATING_ROUTE: "calculate_route",
}


@dataclass(frozen=True)
class TransitionSpec:
    """One source state's declared behaviour."""

    source: WorkflowState
    destinations: frozenset[WorkflowState]
    handler: str
    checkpoint_required: bool = True
    human_input_required: bool = False
    failure_destination: WorkflowState | None = None
    retry_max_attempts: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


# The explicit graph. Each active source lists every legal destination (next step plus
# its typed branch/failure destinations). Paused/terminal states have no outgoing edges.
_S = WorkflowState
TRANSITIONS: dict[WorkflowState, TransitionSpec] = {
    _S.RECEIVED: TransitionSpec(_S.RECEIVED, frozenset({_S.VALIDATING}), "receive"),
    _S.VALIDATING: TransitionSpec(
        _S.VALIDATING,
        frozenset({_S.SANITISING, _S.FAILED_VALIDATION}),
        "validate",
        failure_destination=_S.FAILED_VALIDATION,
    ),
    _S.SANITISING: TransitionSpec(
        _S.SANITISING, frozenset({_S.CLASSIFYING}), "sanitise"
    ),
    _S.CLASSIFYING: TransitionSpec(
        _S.CLASSIFYING,
        frozenset({_S.EXTRACTING_IDENTIFIERS, _S.FAILED_MODEL, _S.ESCALATED}),
        "classify",
        failure_destination=_S.ESCALATED,
        retry_max_attempts=1,
    ),
    _S.EXTRACTING_IDENTIFIERS: TransitionSpec(
        _S.EXTRACTING_IDENTIFIERS,
        frozenset({_S.RESOLVING_CUSTOMER, _S.FAILED_MODEL, _S.ESCALATED}),
        "extract_identifiers",
        failure_destination=_S.ESCALATED,
        retry_max_attempts=1,
    ),
    _S.RESOLVING_CUSTOMER: TransitionSpec(
        _S.RESOLVING_CUSTOMER,
        frozenset({_S.RESOLVING_ORDER, _S.NEEDS_INFORMATION, _S.ESCALATED}),
        "resolve_customer",
        retry_max_attempts=1,
    ),
    _S.RESOLVING_ORDER: TransitionSpec(
        _S.RESOLVING_ORDER,
        frozenset(
            {
                _S.RETRIEVING_ORDER_DATA,
                _S.NEEDS_INFORMATION,
                _S.ESCALATED,
                _S.BLOCKED,
            }
        ),
        "resolve_order",
        retry_max_attempts=1,
    ),
    _S.RETRIEVING_ORDER_DATA: TransitionSpec(
        _S.RETRIEVING_ORDER_DATA,
        frozenset(
            {
                _S.RETRIEVING_POLICY,
                _S.NEEDS_INFORMATION,
                _S.BLOCKED,
                _S.FAILED_DEPENDENCY,
                _S.ESCALATED,
            }
        ),
        "retrieve_order_data",
        failure_destination=_S.FAILED_DEPENDENCY,
        retry_max_attempts=1,
    ),
    _S.RETRIEVING_POLICY: TransitionSpec(
        _S.RETRIEVING_POLICY,
        frozenset({_S.EVALUATING_RULES, _S.ESCALATED, _S.FAILED_DEPENDENCY}),
        "retrieve_policy",
        failure_destination=_S.FAILED_DEPENDENCY,
        retry_max_attempts=2,
    ),
    _S.EVALUATING_RULES: TransitionSpec(
        _S.EVALUATING_RULES,
        frozenset({_S.SUMMARISING_EVIDENCE, _S.NEEDS_INFORMATION, _S.BLOCKED}),
        "evaluate_rules",
    ),
    _S.SUMMARISING_EVIDENCE: TransitionSpec(
        _S.SUMMARISING_EVIDENCE,
        frozenset({_S.DRAFTING_RESPONSE, _S.FAILED_MODEL, _S.ESCALATED}),
        "summarise_evidence",
        failure_destination=_S.ESCALATED,
        retry_max_attempts=1,
    ),
    _S.DRAFTING_RESPONSE: TransitionSpec(
        _S.DRAFTING_RESPONSE,
        frozenset({_S.CALCULATING_ROUTE, _S.FAILED_MODEL, _S.ESCALATED}),
        "draft_response",
        failure_destination=_S.ESCALATED,
        retry_max_attempts=1,
    ),
    _S.CALCULATING_ROUTE: TransitionSpec(
        _S.CALCULATING_ROUTE,
        frozenset(
            {
                _S.AWAITING_AGENT,
                _S.AWAITING_APPROVAL,
                _S.NEEDS_INFORMATION,
                _S.ESCALATED,
                _S.BLOCKED,
                _S.RESOLVED_WITHOUT_ACTION,
            }
        ),
        "calculate_route",
    ),
}


def transition_spec(state: WorkflowState) -> TransitionSpec | None:
    return TRANSITIONS.get(state)


def is_valid_transition(source: WorkflowState, destination: WorkflowState) -> bool:
    """Whether ``source → destination`` is a declared edge (cancellation excluded)."""
    spec = TRANSITIONS.get(source)
    if spec is None:
        return False
    return destination in spec.destinations


def next_handler(state: WorkflowState) -> str | None:
    spec = TRANSITIONS.get(state)
    return spec.handler if spec else None


def can_cancel(state: WorkflowState) -> bool:
    """Any non-terminal run may be cancelled (paused runs included)."""
    return not is_terminal(state)


# Paused and terminal states never have outgoing automatic transitions.
_invalid_sources = (PAUSED_STATES | TERMINAL_STATES) & set(TRANSITIONS)
if _invalid_sources:  # pragma: no cover - module-load invariant
    raise RuntimeError(f"paused/terminal states cannot be sources: {_invalid_sources}")
