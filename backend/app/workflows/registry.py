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
    name=WORKFLOW_NAME, version=WORKFLOW_V1_VERSION, transitions=dict(V1_TRANSITIONS)
)
_V2 = WorkflowDefinition(
    name=WORKFLOW_NAME,
    version=WORKFLOW_V2_VERSION,
    transitions={**V1_TRANSITIONS, **_V2_EXTRA_TRANSITIONS},
)

_REGISTRY: dict[tuple[str, str], WorkflowDefinition] = {
    (_V1.name, _V1.version): _V1,
    (_V2.name, _V2.version): _V2,
}


def get_definition(version: str, name: str = WORKFLOW_NAME) -> WorkflowDefinition:
    try:
        return _REGISTRY[(name, version)]
    except KeyError as exc:
        raise KeyError(f"unknown workflow {name}@{version}") from exc


def registered_versions() -> list[str]:
    return sorted(version for _, version in _REGISTRY)
