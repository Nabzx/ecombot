"""Workflow identity and proposed-action transition unit tests (S6 cleanup)."""

from __future__ import annotations

import pytest
from app.workflows.enums import (
    ProposedActionStatus,
    is_valid_proposed_action_transition,
)
from app.workflows.registry import (
    WORKFLOW_V1_NAME,
    WORKFLOW_V1_VERSION,
    WORKFLOW_V2_NAME,
    WORKFLOW_V2_VERSION,
    canonical_workflow_name,
    display_identity,
    get_definition,
)


def test_canonical_names_track_version() -> None:
    assert canonical_workflow_name(WORKFLOW_V1_VERSION) == WORKFLOW_V1_NAME
    assert canonical_workflow_name(WORKFLOW_V2_VERSION) == WORKFLOW_V2_NAME


def test_display_identity_is_clear_and_legacy_aware() -> None:
    assert display_identity(WORKFLOW_V2_NAME, WORKFLOW_V2_VERSION) == (
        "support-ticket-v2 @ 2.0.0"
    )
    # A legacy v2 row stored under the v1 name still displays its canonical identity.
    assert display_identity(WORKFLOW_V1_NAME, WORKFLOW_V2_VERSION) == (
        "support-ticket-v2 @ 2.0.0"
    )


def test_get_definition_resolves_legacy_and_canonical_v2() -> None:
    canonical = get_definition(WORKFLOW_V2_VERSION, WORKFLOW_V2_NAME)
    legacy = get_definition(WORKFLOW_V2_VERSION, WORKFLOW_V1_NAME)  # legacy stored name
    assert canonical is legacy
    assert canonical.version == WORKFLOW_V2_VERSION


def test_unknown_version_raises() -> None:
    with pytest.raises(KeyError):
        get_definition("9.9.9")


def test_valid_proposed_action_transitions() -> None:
    assert is_valid_proposed_action_transition(
        ProposedActionStatus.AWAITING_APPROVAL,
        ProposedActionStatus.APPROVED_PENDING_EXECUTION,
    )
    assert is_valid_proposed_action_transition(
        ProposedActionStatus.APPROVED_PENDING_EXECUTION,
        ProposedActionStatus.COMPLETED,
    )
    assert is_valid_proposed_action_transition(
        ProposedActionStatus.AWAITING_APPROVAL, ProposedActionStatus.REJECTED
    )


@pytest.mark.parametrize(
    ("source", "destination"),
    [
        # Cannot complete a proposal that was never approved.
        (ProposedActionStatus.AWAITING_APPROVAL, ProposedActionStatus.COMPLETED),
        # Cannot resurrect a rejected proposal.
        (
            ProposedActionStatus.REJECTED,
            ProposedActionStatus.APPROVED_PENDING_EXECUTION,
        ),
        # Cannot jump straight from draft to completed.
        (ProposedActionStatus.DRAFT, ProposedActionStatus.COMPLETED),
        # A completed proposal is terminal.
        (
            ProposedActionStatus.COMPLETED,
            ProposedActionStatus.APPROVED_PENDING_EXECUTION,
        ),
    ],
)
def test_invalid_proposed_action_transitions_rejected(
    source: ProposedActionStatus, destination: ProposedActionStatus
) -> None:
    assert not is_valid_proposed_action_transition(source, destination)
