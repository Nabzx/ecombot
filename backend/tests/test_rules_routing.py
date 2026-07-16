"""Routing tests — interactions between confidence, forced escalation and approval."""

from __future__ import annotations

import pytest
from app.models.enums import TicketCategory
from app.rules.enums import (
    ActionType,
    ApprovalRole,
    DecisionOutcome,
    ReasonCode,
    RiskLevel,
    Route,
)
from app.rules.routing import RoutingInput, calculate_risk_and_route


def _route(**kw: object) -> RoutingInput:
    base: dict[str, object] = {"ticket_category": TicketCategory.order_tracking}
    base.update(kw)
    return RoutingInput.model_validate(base)


@pytest.mark.parametrize(
    ("confidence", "outcome"),
    [
        (0.49, DecisionOutcome.escalate),
        (0.50, DecisionOutcome.requires_review),
        (0.74, DecisionOutcome.requires_review),
        (0.75, DecisionOutcome.eligible),
        (1.0, DecisionOutcome.eligible),
    ],
)
def test_confidence_thresholds_for_readonly(
    confidence: float, outcome: DecisionOutcome
) -> None:
    result = calculate_risk_and_route(
        _route(
            classification_confidence=confidence, proposed_action=ActionType.information
        )
    )
    assert result.outcome == outcome


def test_injection_overrides_high_confidence() -> None:
    result = calculate_risk_and_route(
        _route(classification_confidence=0.99, injection_flag=True)
    )
    assert result.outcome == DecisionOutcome.escalate
    assert result.has(ReasonCode.INJECTION_FORCED_ESCALATION)


def test_unknown_category_overrides_high_confidence() -> None:
    result = calculate_risk_and_route(
        _route(classification_confidence=0.99, ticket_category=TicketCategory.unknown)
    )
    assert result.outcome == DecisionOutcome.escalate
    assert result.has(ReasonCode.UNKNOWN_CATEGORY_ESCALATION)


def test_ownership_mismatch_overrides_all() -> None:
    result = calculate_risk_and_route(
        _route(classification_confidence=0.99, ownership_blocked=True)
    )
    assert result.outcome == DecisionOutcome.blocked
    assert result.route == Route.blocked
    assert result.may_propose is False
    assert result.execution_permitted is False


def test_refund_high_confidence_awaits_supervisor() -> None:
    result = calculate_risk_and_route(
        _route(
            classification_confidence=0.9,
            ticket_category=TicketCategory.refund_request,
            proposed_action=ActionType.refund,
            action_risk=RiskLevel.high,
        )
    )
    assert result.route == Route.await_supervisor
    assert result.required_role == ApprovalRole.supervisor
    assert result.approval_required is True


def test_blocked_action_goes_manual() -> None:
    result = calculate_risk_and_route(
        _route(
            classification_confidence=0.9,
            proposed_action=ActionType.refund,
            action_risk=RiskLevel.blocked,
        )
    )
    assert result.route == Route.manual_handling
    assert result.may_propose is False


def test_multiple_matches_escalate() -> None:
    assert calculate_risk_and_route(
        _route(classification_confidence=0.9, customer_match_count=2)
    ).has(ReasonCode.MULTIPLE_CUSTOMER_MATCHES)
    assert calculate_risk_and_route(
        _route(classification_confidence=0.9, order_match_count=2)
    ).has(ReasonCode.MULTIPLE_ORDER_MATCHES)


def test_policy_conflict_and_missing_escalate() -> None:
    assert calculate_risk_and_route(
        _route(classification_confidence=0.9, policy_conflict=True)
    ).has(ReasonCode.POLICY_CONFLICT)
    assert calculate_risk_and_route(
        _route(classification_confidence=0.9, policy_missing_for_action=True)
    ).has(ReasonCode.NO_SUPPORTING_POLICY)


def test_high_confidence_tracking_may_proceed() -> None:
    result = calculate_risk_and_route(
        _route(classification_confidence=0.9, proposed_action=ActionType.information)
    )
    assert result.route == Route.continue_processing
    assert result.required_role == ApprovalRole.none
    assert result.may_propose is True
