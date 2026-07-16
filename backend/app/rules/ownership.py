"""Ownership rules — the gate that runs before any order-specific decision.

A ticket-linked order must belong to the ticket-linked customer. Ambiguity escalates;
an unresolved customer or order needs more information; an order that belongs to another
customer is blocked (never silently swapped for the conflicting order's owner).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.rules.enums import DecisionOutcome, ReasonCode, RiskLevel, Route
from app.rules.models import RuleResult

RULE_VERSION = "ownership-v1"


class OwnershipInput(BaseModel):
    resolved_customer_id: uuid.UUID | None
    customer_match_count: int = 0
    resolved_order_id: uuid.UUID | None = None
    order_customer_id: uuid.UUID | None = None
    order_match_count: int = 0


def check_ownership(inp: OwnershipInput) -> RuleResult:
    if inp.customer_match_count > 1:
        return _stop(
            DecisionOutcome.escalate,
            Route.escalate,
            ReasonCode.CUSTOMER_MATCH_AMBIGUOUS,
            "More than one customer matches; a human must disambiguate.",
        )
    if inp.resolved_customer_id is None:
        return _stop(
            DecisionOutcome.needs_information,
            Route.needs_information,
            ReasonCode.CUSTOMER_NOT_IDENTIFIED,
            "No customer could be identified from the request.",
            missing=["customer identity"],
        )
    if inp.order_match_count > 1:
        return _stop(
            DecisionOutcome.escalate,
            Route.escalate,
            ReasonCode.ORDER_MATCH_AMBIGUOUS,
            "More than one order matches; a human must disambiguate.",
        )
    if inp.resolved_order_id is None:
        return _stop(
            DecisionOutcome.needs_information,
            Route.needs_information,
            ReasonCode.ORDER_NOT_IDENTIFIED,
            "No order could be identified for this customer.",
            missing=["order reference"],
        )
    if inp.order_customer_id != inp.resolved_customer_id:
        return RuleResult(
            outcome=DecisionOutcome.blocked,
            eligible=False,
            risk_level=RiskLevel.blocked,
            route=Route.blocked,
            reason_codes=[
                ReasonCode.ORDER_OWNERSHIP_MISMATCH,
                ReasonCode.CROSS_CUSTOMER_ACCESS_BLOCKED,
            ],
            explanations=[
                "The order belongs to a different customer; access is blocked."
            ],
            execution_permitted=False,
            rule_version=RULE_VERSION,
        )
    return RuleResult(
        outcome=DecisionOutcome.eligible,
        eligible=True,
        risk_level=RiskLevel.read_only,
        route=Route.continue_processing,
        reason_codes=[ReasonCode.ORDER_OWNERSHIP_CONFIRMED],
        explanations=["The order belongs to the resolved customer."],
        execution_permitted=False,
        rule_version=RULE_VERSION,
    )


def is_confirmed(result: RuleResult) -> bool:
    return result.has(ReasonCode.ORDER_OWNERSHIP_CONFIRMED)


def _stop(
    outcome: DecisionOutcome,
    route: Route,
    code: ReasonCode,
    explanation: str,
    *,
    missing: list[str] | None = None,
) -> RuleResult:
    return RuleResult(
        outcome=outcome,
        eligible=False,
        risk_level=RiskLevel.read_only,
        route=route,
        reason_codes=[code],
        explanations=[explanation],
        missing_information=missing or [],
        execution_permitted=False,
        rule_version=RULE_VERSION,
    )
