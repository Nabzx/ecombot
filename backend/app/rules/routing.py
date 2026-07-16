"""Deterministic risk-and-routing rule.

Consumes structured facts (never raw customer text) and decides the route, the required
human role, and whether an action may be proposed. Ownership blocks everything; a set of
forced conditions escalate regardless of confidence; otherwise confidence thresholds and
the proposed action's risk determine routing and approval.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.models.enums import TicketCategory
from app.rules.constants import CONFIDENCE_AGENT_REVIEW, CONFIDENCE_CONTINUE
from app.rules.enums import (
    ActionType,
    ApprovalRole,
    DecisionOutcome,
    ReasonCode,
    RiskLevel,
    Route,
)
from app.rules.models import RuleResult

RULE_VERSION = "routing-v1"

_SUPERVISOR_ACTIONS = {
    ActionType.return_rma,
    ActionType.replacement,
    ActionType.refund,
    ActionType.cancellation,
}


class RoutingInput(BaseModel):
    classification_confidence: float | None = None
    customer_match_count: int = 1
    order_match_count: int = 1
    ownership_blocked: bool = False
    injection_flag: bool = False
    ticket_category: TicketCategory
    policy_conflict: bool = False
    policy_missing_for_action: bool = False
    proposed_action: ActionType = ActionType.information
    action_risk: RiskLevel = RiskLevel.read_only
    missing_information: bool = False
    dependency_failure: bool = False
    delivered_but_disputed: bool = False
    unverifiable_incorrect_item: bool = False


def _forced_escalation_codes(inp: RoutingInput) -> list[ReasonCode]:
    codes: list[ReasonCode] = []
    if inp.injection_flag:
        codes.append(ReasonCode.INJECTION_FORCED_ESCALATION)
    if inp.customer_match_count > 1:
        codes.append(ReasonCode.MULTIPLE_CUSTOMER_MATCHES)
    if inp.order_match_count > 1:
        codes.append(ReasonCode.MULTIPLE_ORDER_MATCHES)
    if inp.delivered_but_disputed:
        codes.append(ReasonCode.MISSING_DELIVERY_ESCALATION_REQUIRED)
    if inp.policy_conflict:
        codes.append(ReasonCode.POLICY_CONFLICT)
    if inp.policy_missing_for_action:
        codes.append(ReasonCode.NO_SUPPORTING_POLICY)
    if inp.ticket_category == TicketCategory.unknown:
        codes.append(ReasonCode.UNKNOWN_CATEGORY_ESCALATION)
    if inp.dependency_failure:
        codes.append(ReasonCode.DEPENDENCY_FAILURE_ESCALATION)
    if inp.unverifiable_incorrect_item:
        codes.append(ReasonCode.ITEM_MISMATCH_UNVERIFIED)
    return codes


def _required_role(action: ActionType) -> ApprovalRole:
    if action in _SUPERVISOR_ACTIONS:
        return ApprovalRole.supervisor
    if action == ActionType.ticket_status_update:
        return ApprovalRole.agent
    return ApprovalRole.none


def calculate_risk_and_route(inp: RoutingInput) -> RuleResult:
    # 1. Ownership block beats everything.
    if inp.ownership_blocked:
        return _result(
            DecisionOutcome.blocked,
            Route.blocked,
            RiskLevel.blocked,
            [ReasonCode.ORDER_OWNERSHIP_MISMATCH],
            ["Ownership mismatch blocks any action."],
            role=ApprovalRole.none,
            may_propose=False,
        )

    # 2. Forced escalations (regardless of confidence).
    forced = _forced_escalation_codes(inp)
    if forced:
        return _result(
            DecisionOutcome.escalate,
            Route.escalate,
            RiskLevel.high
            if inp.action_risk == RiskLevel.blocked
            else RiskLevel.medium,
            forced,
            ["One or more conditions force escalation."],
            role=ApprovalRole.none,
            may_propose=False,
        )

    # 3. Blocked-risk action cannot be executed in-system.
    if inp.action_risk == RiskLevel.blocked:
        return _result(
            DecisionOutcome.blocked,
            Route.manual_handling,
            RiskLevel.blocked,
            [ReasonCode.REFUND_OVER_SYSTEM_LIMIT],
            ["The proposed action exceeds system limits; manual handling."],
            role=ApprovalRole.none,
            may_propose=False,
        )

    # 4. Confidence gating.
    confidence = inp.classification_confidence
    if confidence is None or confidence < CONFIDENCE_AGENT_REVIEW:
        return _result(
            DecisionOutcome.escalate,
            Route.escalate,
            RiskLevel.medium,
            [ReasonCode.CONFIDENCE_TOO_LOW],
            ["Classification confidence is too low to proceed."],
            role=ApprovalRole.none,
            may_propose=False,
        )

    # 5. Missing information short-circuits to a clarification.
    if inp.missing_information:
        return _result(
            DecisionOutcome.needs_information,
            Route.needs_information,
            RiskLevel.read_only,
            [ReasonCode.AGENT_REVIEW_REQUIRED],
            ["More information is required before proceeding."],
            role=ApprovalRole.agent,
            may_propose=False,
        )

    role = _required_role(inp.proposed_action)
    agent_review = confidence < CONFIDENCE_CONTINUE
    confidence_code = (
        ReasonCode.CONFIDENCE_SUFFICIENT
        if not agent_review
        else ReasonCode.CONFIDENCE_AGENT_REVIEW
    )

    if role == ApprovalRole.supervisor:
        return _result(
            DecisionOutcome.requires_approval,
            Route.await_supervisor,
            inp.action_risk,
            [confidence_code, ReasonCode.SUPERVISOR_APPROVAL_REQUIRED],
            ["The action requires Supervisor approval."],
            role=role,
            approval_required=True,
            may_propose=True,
        )
    if role == ApprovalRole.agent or agent_review:
        return _result(
            DecisionOutcome.requires_review,
            Route.await_agent,
            inp.action_risk,
            [confidence_code, ReasonCode.AGENT_REVIEW_REQUIRED],
            ["The action awaits Agent review."],
            role=ApprovalRole.agent,
            may_propose=True,
        )
    return _result(
        DecisionOutcome.eligible,
        Route.continue_processing,
        inp.action_risk,
        [
            confidence_code,
            ReasonCode.NO_APPROVAL_REQUIRED,
            ReasonCode.ACTION_MAY_PROCEED,
        ],
        ["Read-only, high-confidence request; no approval required."],
        role=ApprovalRole.none,
        may_propose=True,
    )


def _result(
    outcome: DecisionOutcome,
    route: Route,
    risk: RiskLevel,
    codes: list[ReasonCode],
    explanations: list[str],
    *,
    role: ApprovalRole,
    approval_required: bool = False,
    may_propose: bool = True,
) -> RuleResult:
    return RuleResult(
        outcome=outcome,
        route=route,
        risk_level=risk,
        reason_codes=codes,
        explanations=explanations,
        approval_required=approval_required,
        execution_permitted=False,  # never in S2
        required_role=role,
        may_propose=may_propose,
        rule_version=RULE_VERSION,
    )
