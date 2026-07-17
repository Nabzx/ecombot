"""State-machine and checkpoint unit tests (no database)."""

from __future__ import annotations

import uuid

import pytest
from app.workflows.checkpointing import (
    CheckpointError,
    build_snapshot,
    restore_state,
    verify_checkpoint,
)
from app.workflows.definition import (
    STATE_SCHEMA_VERSION,
    TRANSITIONS,
    WORKFLOW_NAME,
    WORKFLOW_VERSION,
    can_cancel,
    is_valid_transition,
    next_handler,
)
from app.workflows.enums import (
    ACTIVE_STATES,
    PAUSED_STATES,
    TERMINAL_STATES,
    WorkflowState,
    WorkflowStatus,
    status_for_state,
)
from app.workflows.state import SupportWorkflowState, snapshot_hash


def _state() -> SupportWorkflowState:
    return SupportWorkflowState(
        workflow_run_id=uuid.uuid4(),
        workflow_name=WORKFLOW_NAME,
        workflow_version=WORKFLOW_VERSION,
        ticket_id=uuid.uuid4(),
        ticket_reference="TKT-2026-000001",
        correlation_id="corr-1",
    )


def test_state_partition_is_disjoint_and_complete() -> None:
    assert not (ACTIVE_STATES & PAUSED_STATES)
    assert not (ACTIVE_STATES & TERMINAL_STATES)
    assert not (PAUSED_STATES & TERMINAL_STATES)
    assert set(WorkflowState) == ACTIVE_STATES | PAUSED_STATES | TERMINAL_STATES


def test_valid_transitions_declared() -> None:
    assert is_valid_transition(WorkflowState.RECEIVED, WorkflowState.VALIDATING)
    assert is_valid_transition(
        WorkflowState.CALCULATING_ROUTE, WorkflowState.AWAITING_APPROVAL
    )


def test_invalid_transition_rejected() -> None:
    assert not is_valid_transition(WorkflowState.RECEIVED, WorkflowState.BLOCKED)
    assert not is_valid_transition(
        WorkflowState.CLASSIFYING, WorkflowState.AWAITING_AGENT
    )


def test_paused_and_terminal_states_have_no_transitions() -> None:
    for state in PAUSED_STATES | TERMINAL_STATES:
        assert state not in TRANSITIONS
        assert next_handler(state) is None


@pytest.mark.parametrize("state", sorted(WorkflowState, key=str))
def test_status_matches_state_partition(state: WorkflowState) -> None:
    status = status_for_state(state)
    if state in PAUSED_STATES:
        assert status == WorkflowStatus.PAUSED
    elif state == WorkflowState.CANCELLED:
        assert status == WorkflowStatus.CANCELLED
    elif state == WorkflowState.RESOLVED_WITHOUT_ACTION:
        assert status == WorkflowStatus.COMPLETED
    elif state in TERMINAL_STATES:
        assert status == WorkflowStatus.FAILED


def test_terminal_states_cannot_be_cancelled() -> None:
    for state in TERMINAL_STATES:
        assert not can_cancel(state)
    for state in ACTIVE_STATES | PAUSED_STATES:
        assert can_cancel(state)


def test_snapshot_hash_is_stable() -> None:
    state = _state()
    assert snapshot_hash(state.snapshot()) == snapshot_hash(state.snapshot())


def test_different_states_produce_different_hashes() -> None:
    a = _state()
    b = a.model_copy(update={"current_state": WorkflowState.CLASSIFYING})
    assert snapshot_hash(a.snapshot()) != snapshot_hash(b.snapshot())


def test_checkpoint_round_trip() -> None:
    state = _state()
    snapshot, digest = build_snapshot(state)
    verify_checkpoint(snapshot, digest, STATE_SCHEMA_VERSION)
    restored = restore_state(snapshot)
    assert restored.workflow_run_id == state.workflow_run_id


def test_tampered_checkpoint_rejected() -> None:
    state = _state()
    snapshot, digest = build_snapshot(state)
    snapshot["step_index"] = 99
    with pytest.raises(CheckpointError):
        verify_checkpoint(snapshot, digest, STATE_SCHEMA_VERSION)


def test_unsupported_schema_version_rejected() -> None:
    state = _state()
    snapshot, digest = build_snapshot(state)
    with pytest.raises(CheckpointError):
        verify_checkpoint(snapshot, digest, "workflow-state-v999")


def test_snapshot_preserves_structural_ids() -> None:
    state = _state().model_copy(
        update={
            "raw_customer_message": "call me on 07911123456 or card 4111111111111111"
        }
    )
    snapshot, _ = build_snapshot(state)
    # PII in free text is redacted, but the UUID/reference identity survives intact.
    assert snapshot["workflow_run_id"] == str(state.workflow_run_id)
    assert snapshot["ticket_id"] == str(state.ticket_id)
    assert "07911123456" not in str(snapshot["raw_customer_message"])


def test_no_execute_state_exists() -> None:
    values = {s.value for s in WorkflowState}
    assert not any(v.startswith("executing_") for v in values)
    assert "resolved_without_action" in values
