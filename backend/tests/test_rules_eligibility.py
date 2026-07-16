"""Boundary tests for return, refund, cancellation and remedy rules (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.models.enums import OrderStatus, ShipmentStatus
from app.rules.cancellations import CancellationInput, check_cancellation_eligibility
from app.rules.clock import FixedClock
from app.rules.enums import (
    DecisionOutcome,
    ItemCondition,
    ReasonCode,
    RefundBasis,
    ReturnReason,
    RiskLevel,
)
from app.rules.refunds import RefundInput, check_refund_eligibility
from app.rules.remedies import (
    DamagedItemInput,
    IncorrectItemInput,
    check_damaged_item_remedy,
    check_incorrect_item_remedy,
)
from app.rules.returns import ReturnInput, check_return_eligibility

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
CLOCK = FixedClock(NOW)
TID = uuid.UUID(int=1)
OID = uuid.UUID(int=2)


def _return(days_ago: int, **kw: object) -> ReturnInput:
    return ReturnInput(
        ownership_confirmed=True,
        order_status=OrderStatus.delivered,
        delivered_at=NOW - timedelta(days=days_ago),
        reason=kw.get("reason", ReturnReason.changed_mind),
        condition=kw.get("condition", ItemCondition.unused),
        already_returned=bool(kw.get("already_returned", False)),
    )


@pytest.mark.parametrize(
    ("days_ago", "eligible"),
    [(29, True), (30, True), (31, False)],
)
def test_return_window_boundary(days_ago: int, eligible: bool) -> None:
    result = check_return_eligibility(_return(days_ago), CLOCK)
    assert (result.outcome == DecisionOutcome.eligible) is eligible
    expected = (
        ReasonCode.RETURN_WITHIN_WINDOW
        if eligible
        else ReasonCode.RETURN_WINDOW_EXPIRED
    )
    assert result.has(expected)


def test_return_not_delivered() -> None:
    inp = _return(5).model_copy(update={"order_status": OrderStatus.processing})
    result = check_return_eligibility(inp, CLOCK)
    assert result.has(ReasonCode.RETURN_NOT_YET_APPLICABLE)


def test_return_missing_delivery_date() -> None:
    inp = _return(5).model_copy(update={"delivered_at": None})
    result = check_return_eligibility(inp, CLOCK)
    assert result.outcome == DecisionOutcome.needs_information
    assert result.has(ReasonCode.DELIVERY_DATE_MISSING)


def test_return_already_returned() -> None:
    result = check_return_eligibility(_return(5, already_returned=True), CLOCK)
    assert result.has(ReasonCode.ITEM_ALREADY_RETURNED)


def test_changed_mind_requires_unused() -> None:
    used = check_return_eligibility(_return(5, condition=ItemCondition.used), CLOCK)
    assert used.has(ReasonCode.ITEM_CONDITION_NOT_ELIGIBLE)
    unused = check_return_eligibility(_return(5, condition=ItemCondition.unused), CLOCK)
    assert unused.outcome == DecisionOutcome.eligible


def test_damaged_and_incorrect_exceptions_ignore_condition() -> None:
    damaged = check_return_eligibility(
        _return(5, reason=ReturnReason.damaged, condition=ItemCondition.used), CLOCK
    )
    assert damaged.outcome == DecisionOutcome.eligible
    assert damaged.has(ReasonCode.DAMAGED_ITEM_EXCEPTION)
    incorrect = check_return_eligibility(
        _return(5, reason=ReturnReason.incorrect_item, condition=ItemCondition.used),
        CLOCK,
    )
    assert incorrect.has(ReasonCode.INCORRECT_ITEM_EXCEPTION)


def _refund(
    amount: int, *, item: int = 30_000, paid: int = 30_000, prior: int = 0
) -> RefundInput:
    return RefundInput(
        ownership_confirmed=True,
        ticket_id=TID,
        order_id=OID,
        requested_amount_pence=amount,
        item_line_total_pence=item,
        order_total_paid_pence=paid,
        prior_refunded_pence=prior,
        basis=RefundBasis.damaged_item,
    )


@pytest.mark.parametrize(
    ("amount", "outcome", "risk"),
    [
        (0, DecisionOutcome.ineligible, RiskLevel.read_only),
        (1, DecisionOutcome.requires_approval, RiskLevel.medium),
        (5_000, DecisionOutcome.requires_approval, RiskLevel.medium),
        (5_001, DecisionOutcome.requires_approval, RiskLevel.high),
        (25_000, DecisionOutcome.requires_approval, RiskLevel.high),
        (25_001, DecisionOutcome.blocked, RiskLevel.blocked),
    ],
)
def test_refund_amount_bands(
    amount: int, outcome: DecisionOutcome, risk: RiskLevel
) -> None:
    result = check_refund_eligibility(_refund(amount))
    assert result.outcome == outcome
    assert result.risk_level == risk


def test_refund_zero_is_invalid_amount() -> None:
    assert check_refund_eligibility(_refund(0)).has(ReasonCode.REFUND_AMOUNT_INVALID)


def test_refund_item_total_boundary() -> None:
    ok = check_refund_eligibility(_refund(10_000, item=10_000))
    assert ok.outcome == DecisionOutcome.requires_approval
    over = check_refund_eligibility(_refund(10_001, item=10_000))
    assert over.has(ReasonCode.REFUND_EXCEEDS_ITEM_TOTAL)


def test_refund_remaining_balance_boundary() -> None:
    # item is large, but remaining order balance is the binding constraint.
    ok = check_refund_eligibility(_refund(4_000, item=30_000, paid=10_000, prior=6_000))
    assert ok.outcome == DecisionOutcome.requires_approval
    over = check_refund_eligibility(
        _refund(4_001, item=30_000, paid=10_000, prior=6_000)
    )
    assert over.has(ReasonCode.REFUND_EXCEEDS_REMAINING_ORDER_BALANCE)


def test_refund_all_require_supervisor_and_idempotency() -> None:
    result = check_refund_eligibility(_refund(1_000))
    assert result.approval_required is True
    assert result.has(ReasonCode.REFUND_SUPERVISOR_APPROVAL_REQUIRED)
    assert result.execution_permitted is False
    assert result.idempotency_key is not None


def test_refund_standard_return_requires_received_item() -> None:
    inp = _refund(1_000).model_copy(update={"basis": RefundBasis.returned_item})
    assert check_refund_eligibility(inp).has(ReasonCode.RETURN_NOT_RECEIVED)
    received = inp.model_copy(update={"return_received": True})
    assert (
        check_refund_eligibility(received).outcome == DecisionOutcome.requires_approval
    )


def test_refund_unsupported_reason() -> None:
    inp = _refund(1_000).model_copy(update={"basis": RefundBasis.unsupported})
    assert check_refund_eligibility(inp).has(ReasonCode.REFUND_REASON_UNSUPPORTED)


def test_refund_ownership_blocks() -> None:
    inp = _refund(1_000).model_copy(update={"ownership_confirmed": False})
    result = check_refund_eligibility(inp)
    assert result.outcome == DecisionOutcome.blocked
    assert result.execution_permitted is False


def _cancel(status: OrderStatus, shipment: ShipmentStatus | None) -> CancellationInput:
    return CancellationInput(
        ownership_confirmed=True,
        ticket_id=TID,
        order_id=OID,
        order_status=status,
        shipment_status=shipment,
        shipment_present=shipment is not None,
    )


@pytest.mark.parametrize(
    ("status", "shipment", "eligible"),
    [
        (OrderStatus.placed, None, True),
        (OrderStatus.paid, ShipmentStatus.label_created, True),
        (OrderStatus.processing, ShipmentStatus.label_created, True),
        (OrderStatus.processing, ShipmentStatus.in_transit, False),
        (OrderStatus.shipped, ShipmentStatus.in_transit, False),
        (OrderStatus.delivered, ShipmentStatus.delivered, False),
        (OrderStatus.cancelled, None, False),
        (OrderStatus.refunded, ShipmentStatus.delivered, False),
    ],
)
def test_cancellation_matrix(
    status: OrderStatus, shipment: ShipmentStatus | None, eligible: bool
) -> None:
    result = check_cancellation_eligibility(_cancel(status, shipment))
    assert (result.outcome == DecisionOutcome.requires_approval) is eligible
    if eligible:
        assert result.risk_level == RiskLevel.high
        assert result.approval_required is True


def test_cancellation_already_cancelled_is_idempotent_not_error() -> None:
    result = check_cancellation_eligibility(_cancel(OrderStatus.cancelled, None))
    assert result.outcome == DecisionOutcome.ineligible
    assert result.has(ReasonCode.ORDER_ALREADY_CANCELLED)


def test_cancellation_shipped_recommends_return() -> None:
    result = check_cancellation_eligibility(
        _cancel(OrderStatus.shipped, ShipmentStatus.in_transit)
    )
    assert result.has(ReasonCode.RETURN_FLOW_RECOMMENDED)


def test_damaged_remedy_within_and_outside_window() -> None:
    inside = check_damaged_item_remedy(
        DamagedItemInput(
            ownership_confirmed=True, delivered_at=NOW - timedelta(days=10)
        ),
        CLOCK,
    )
    assert inside.outcome == DecisionOutcome.requires_approval
    assert inside.has(ReasonCode.DAMAGED_ITEM_REMEDY_ELIGIBLE)
    outside = check_damaged_item_remedy(
        DamagedItemInput(
            ownership_confirmed=True, delivered_at=NOW - timedelta(days=40)
        ),
        CLOCK,
    )
    assert outside.has(ReasonCode.DAMAGED_ITEM_REPORT_OUTSIDE_WINDOW)


def _incorrect(claimed: str | None) -> IncorrectItemInput:
    return IncorrectItemInput(
        ownership_confirmed=True,
        delivered_at=NOW - timedelta(days=5),
        ordered_sku="MER-KIT-001",
        claimed_received_sku=claimed,
    )


def test_incorrect_remedy_sku_logic() -> None:
    unknown = check_incorrect_item_remedy(_incorrect(None), CLOCK)
    assert unknown.outcome == DecisionOutcome.escalate
    assert unknown.has(ReasonCode.RECEIVED_SKU_UNKNOWN)
    same = check_incorrect_item_remedy(_incorrect("MER-KIT-001"), CLOCK)
    assert same.has(ReasonCode.RECEIVED_SKU_MATCHES_ORDER)
    diff = check_incorrect_item_remedy(_incorrect("MER-DEC-002"), CLOCK)
    assert diff.outcome == DecisionOutcome.requires_approval
    assert diff.has(ReasonCode.INCORRECT_ITEM_REMEDY_ELIGIBLE)
