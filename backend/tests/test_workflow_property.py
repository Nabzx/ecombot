"""Property-based tests for workflow invariants."""

from __future__ import annotations

import uuid

from app.workflows.definition import (
    WORKFLOW_NAME,
    WORKFLOW_VERSION,
    is_valid_transition,
)
from app.workflows.enums import (
    TERMINAL_STATES,
    WorkflowState,
    is_terminal,
)
from app.workflows.state import SupportWorkflowState, snapshot_hash
from hypothesis import given
from hypothesis import strategies as st

_STATES = list(WorkflowState)


@given(source=st.sampled_from(_STATES), dest=st.sampled_from(_STATES))
def test_terminal_states_never_transition(
    source: WorkflowState, dest: WorkflowState
) -> None:
    if is_terminal(source):
        assert not is_valid_transition(source, dest)


@given(source=st.sampled_from(_STATES), dest=st.sampled_from(_STATES))
def test_transition_validity_is_membership(
    source: WorkflowState, dest: WorkflowState
) -> None:
    from app.workflows.definition import TRANSITIONS

    valid = is_valid_transition(source, dest)
    spec = TRANSITIONS.get(source)
    assert valid == (spec is not None and dest in spec.destinations)


@given(reference=st.text(min_size=1, max_size=20))
def test_snapshot_hash_stable_for_equal_state(reference: str) -> None:
    state = SupportWorkflowState(
        workflow_run_id=uuid.uuid4(),
        workflow_name=WORKFLOW_NAME,
        workflow_version=WORKFLOW_VERSION,
        ticket_id=uuid.uuid4(),
        ticket_reference=reference,
        correlation_id="c",
    )
    assert snapshot_hash(state.snapshot()) == snapshot_hash(state.snapshot())


@given(step_index=st.integers(min_value=0, max_value=1000))
def test_step_index_is_reflected_in_hash(step_index: int) -> None:
    base = SupportWorkflowState(
        workflow_run_id=uuid.uuid4(),
        workflow_name=WORKFLOW_NAME,
        workflow_version=WORKFLOW_VERSION,
        ticket_id=uuid.uuid4(),
        ticket_reference="TKT-1",
        correlation_id="c",
    )
    other = base.model_copy(update={"step_index": step_index + 1})
    if step_index + 1 != base.step_index:
        assert snapshot_hash(base.snapshot()) != snapshot_hash(other.snapshot())


def test_all_terminal_states_are_terminal() -> None:
    for state in TERMINAL_STATES:
        assert is_terminal(state)
