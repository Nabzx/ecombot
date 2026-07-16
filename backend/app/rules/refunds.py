"""Refund eligibility and limit rules. Nothing is executed or persisted here.

Prior refund history is supplied as an integer (the tool layer fetches it via
``RefundHistoryPort``); the rule is a pure function so every boundary is testable. All
refunds require Supervisor approval; amount bands set the risk level.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from pydantic import BaseModel

from app.rules.constants import REFUND_HIGH_MAX_PENCE, REFUND_MEDIUM_MAX_PENCE
from app.rules.enums import (
    ActionType,
    DecisionOutcome,
    ReasonCode,
    RefundBasis,
    RiskLevel,
    Route,
)
from app.rules.idempotency import generate_idempotency_key
from app.rules.models import RuleResult

RULE_VERSION = "refund-v1"


class RefundHistoryPort(Protocol):
    async def refunded_total_pence(self, order_id: uuid.UUID) -> int: ...


class NoRefundHistory:
    """Adapter for the current seeded data, where no refunds have executed."""

    async def refunded_total_pence(self, order_id: uuid.UUID) -> int:
        return 0


class RefundInput(BaseModel):
    ownership_confirmed: bool
    ticket_id: uuid.UUID
    order_id: uuid.UUID
    requested_amount_pence: int
    item_line_total_pence: int
    order_total_paid_pence: int
    prior_refunded_pence: int = 0
    basis: RefundBasis
    return_received: bool = False


def calculate_refund_limit(inp: RefundInput) -> int:
    """Maximum refundable: the lower of item total and remaining order balance."""
    remaining = max(0, inp.order_total_paid_pence - inp.prior_refunded_pence)
    return min(inp.item_line_total_pence, remaining)


def check_refund_eligibility(inp: RefundInput) -> RuleResult:
    remaining = max(0, inp.order_total_paid_pence - inp.prior_refunded_pence)
    max_refundable = min(inp.item_line_total_pence, remaining)
    computed = {
        "requested_refund_pence": inp.requested_amount_pence,
        "maximum_refund_pence": max_refundable,
        "item_line_total_pence": inp.item_line_total_pence,
        "remaining_order_balance_pence": remaining,
        "prior_refunded_pence": inp.prior_refunded_pence,
    }

    if not inp.ownership_confirmed:
        return _blocked(ReasonCode.ORDER_OWNERSHIP_MISMATCH, computed)

    if inp.basis == RefundBasis.unsupported:
        return _ineligible(
            ReasonCode.REFUND_REASON_UNSUPPORTED,
            "The stated reason does not support a refund.",
            computed,
        )
    if inp.requested_amount_pence <= 0:
        return _ineligible(
            ReasonCode.REFUND_AMOUNT_INVALID,
            "The requested refund amount must be positive.",
            computed,
        )
    if inp.requested_amount_pence > inp.item_line_total_pence:
        return _ineligible(
            ReasonCode.REFUND_EXCEEDS_ITEM_TOTAL,
            "The requested amount exceeds the item's line total.",
            computed,
        )
    if inp.requested_amount_pence > remaining:
        return _ineligible(
            ReasonCode.REFUND_EXCEEDS_REMAINING_ORDER_BALANCE,
            "The requested amount exceeds the remaining order balance.",
            computed,
        )
    if inp.basis == RefundBasis.returned_item and not inp.return_received:
        return _ineligible(
            ReasonCode.RETURN_NOT_RECEIVED,
            "A standard return refund requires the returned item to be received.",
            computed,
        )

    idempotency_key = generate_idempotency_key(
        ticket_id=inp.ticket_id,
        action_type=ActionType.refund,
        order_id=inp.order_id,
        amount_pence=inp.requested_amount_pence,
    )

    # Amount above the system limit is blocked (manual finance handling).
    if inp.requested_amount_pence > REFUND_HIGH_MAX_PENCE:
        return RuleResult(
            outcome=DecisionOutcome.blocked,
            eligible=False,
            risk_level=RiskLevel.blocked,
            route=Route.manual_handling,
            reason_codes=[
                ReasonCode.REFUND_OVER_SYSTEM_LIMIT,
                ReasonCode.REFUND_BLOCKED,
            ],
            explanations=[
                "Refunds above £250 are handled by finance outside the support flow."
            ],
            computed=computed,
            approval_required=False,
            execution_permitted=False,
            rule_version=RULE_VERSION,
            idempotency_key=idempotency_key,
        )

    if inp.requested_amount_pence <= REFUND_MEDIUM_MAX_PENCE:
        risk = RiskLevel.medium
        band_code = ReasonCode.REFUND_MEDIUM_RISK
    else:
        risk = RiskLevel.high
        band_code = ReasonCode.REFUND_HIGH_RISK

    return RuleResult(
        outcome=DecisionOutcome.requires_approval,
        eligible=True,
        risk_level=risk,
        route=Route.await_supervisor,
        reason_codes=[
            ReasonCode.REFUND_ELIGIBLE,
            band_code,
            ReasonCode.REFUND_SUPERVISOR_APPROVAL_REQUIRED,
            ReasonCode.DUPLICATE_CHECK_REQUIRED,
        ],
        explanations=[
            "The refund is within limits.",
            "All refunds require Supervisor approval.",
            "Duplicate execution must be checked before the refund is issued.",
        ],
        computed=computed,
        approval_required=True,
        execution_permitted=False,
        rule_version=RULE_VERSION,
        idempotency_key=idempotency_key,
    )


def _ineligible(
    code: ReasonCode, explanation: str, computed: dict[str, int]
) -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.ineligible,
        eligible=False,
        risk_level=RiskLevel.read_only,
        route=Route.continue_processing,
        reason_codes=[code],
        explanations=[explanation],
        computed=computed,
        execution_permitted=False,
        rule_version=RULE_VERSION,
    )


def _blocked(code: ReasonCode, computed: dict[str, int]) -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.blocked,
        eligible=False,
        risk_level=RiskLevel.blocked,
        route=Route.blocked,
        reason_codes=[code, ReasonCode.REFUND_BLOCKED],
        explanations=["Ownership is not confirmed; the refund is blocked."],
        computed=computed,
        execution_permitted=False,
        rule_version=RULE_VERSION,
    )
