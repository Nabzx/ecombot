"""Workflow definition registry: ``support-ticket-v1`` and ``support-ticket-v2``.

v1 is frozen (S5). v2 reuses every v1 processing stage and adds the approval/execution
continuation from ``awaiting_approval``. A run's version is never changed in place; new
ordinary runs default to v2, and replays keep their source version. The runner only
auto-advances *active* states — the approval/execution transitions are driven by human
decisions and the outbox worker, never by the normal runner loop.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.workflows.definition import (
    STATE_SCHEMA_VERSION,
    WORKFLOW_NAME,
    TransitionSpec,
)
from app.workflows.definition import (
    TRANSITIONS as V1_TRANSITIONS,
)
from app.workflows.enums import WorkflowState

WORKFLOW_V1_VERSION = "1.0.0"
WORKFLOW_V2_VERSION = "2.0.0"
DEFAULT_WORKFLOW_VERSION = WORKFLOW_V2_VERSION

# Canonical workflow names. Historically both v1 and v2 shared WORKFLOW_NAME
# ("support-ticket-v1"), so early v2 runs were persisted as support-ticket-v1@2.0.0.
# New v2 runs use the canonical name below; legacy rows stay readable via the resolver.
WORKFLOW_V1_NAME = "support-ticket-v1"
WORKFLOW_V2_NAME = "support-ticket-v2"
LEGACY_V2_NAME = WORKFLOW_NAME  # "support-ticket-v1" — how early v2 runs were stored


def canonical_workflow_name(version: str) -> str:
    """The canonical workflow name for a semantic version."""
    return WORKFLOW_V2_NAME if version == WORKFLOW_V2_VERSION else WORKFLOW_V1_NAME


def display_identity(name: str, version: str) -> str:
    """A clear, human-readable ``name @ version`` for CLI/API, legacy-aware.

    A legacy v2 row stored as ``support-ticket-v1@2.0.0`` is shown with its canonical
    name so the identity always reads correctly, without rewriting the stored row.
    """
    return f"{canonical_workflow_name(version)} @ {version}"


_S = WorkflowState

# v2 = every v1 edge plus the approval/execution continuation. These edges are validated
# by the state machine but triggered by decisions / the worker, not the runner loop.
_V2_EXTRA_TRANSITIONS: dict[WorkflowState, TransitionSpec] = {
    _S.AWAITING_APPROVAL: TransitionSpec(
        _S.AWAITING_APPROVAL,
        frozenset(
            {
                _S.APPROVED_PENDING_EXECUTION,
                _S.APPROVAL_REJECTED,
                _S.APPROVAL_EXPIRED,
                # A withdrawn (agent-cancelled) proposal returns to a human agent
                # rather than terminating the run.
                _S.AWAITING_AGENT,
                # An approved action that is not automatically executable (e.g. a
                # replacement) is routed to a human for manual handling.
                _S.MANUAL_ACTION_REQUIRED,
            }
        ),
        handler="__human__",
        human_input_required=True,
    ),
    _S.APPROVED_PENDING_EXECUTION: TransitionSpec(
        _S.APPROVED_PENDING_EXECUTION,
        frozenset({_S.EXECUTING_ACTION}),
        handler="__worker__",
    ),
    _S.EXECUTING_ACTION: TransitionSpec(
        _S.EXECUTING_ACTION,
        frozenset(
            {
                _S.ACTION_SUCCEEDED,
                _S.ACTION_FAILED,
                _S.MANUAL_ACTION_REQUIRED,
            }
        ),
        handler="__worker__",
    ),
    _S.APPROVAL_EXPIRED: TransitionSpec(
        _S.APPROVAL_EXPIRED,
        frozenset({_S.AWAITING_APPROVAL}),
        handler="__human__",
        human_input_required=True,
    ),
    _S.ACTION_FAILED: TransitionSpec(
        _S.ACTION_FAILED,
        frozenset({_S.APPROVED_PENDING_EXECUTION, _S.MANUAL_ACTION_REQUIRED}),
        handler="__worker__",
    ),
}


@dataclass(frozen=True)
class WorkflowDefinition:
    """A named, versioned workflow: its transition table and state-schema version."""

    name: str
    version: str
    transitions: dict[WorkflowState, TransitionSpec]
    state_schema_version: str = STATE_SCHEMA_VERSION

    def is_valid_transition(
        self, source: WorkflowState, destination: WorkflowState
    ) -> bool:
        spec = self.transitions.get(source)
        return spec is not None and destination in spec.destinations


_V1 = WorkflowDefinition(
    name=WORKFLOW_V1_NAME, version=WORKFLOW_V1_VERSION, transitions=dict(V1_TRANSITIONS)
)
_V2 = WorkflowDefinition(
    name=WORKFLOW_V2_NAME,
    version=WORKFLOW_V2_VERSION,
    transitions={**V1_TRANSITIONS, **_V2_EXTRA_TRANSITIONS},
)

# A definition is keyed by version (the transition table is a function of the version).
# Both the canonical and the legacy v2 name resolve to the same definition, so early
# support-ticket-v1@2.0.0 runs stay fully inspectable and replayable.
_BY_VERSION: dict[str, WorkflowDefinition] = {
    _V1.version: _V1,
    _V2.version: _V2,
}


def get_definition(version: str, name: str = WORKFLOW_V1_NAME) -> WorkflowDefinition:
    """Resolve a workflow definition by version (name is advisory, legacy-tolerant)."""
    try:
        return _BY_VERSION[version]
    except KeyError as exc:
        raise KeyError(f"unknown workflow {name}@{version}") from exc


def registered_versions() -> list[str]:
    return sorted(_BY_VERSION)
