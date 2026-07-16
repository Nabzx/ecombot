"""Property-based tests for deterministic invariants (Hypothesis)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.models.enums import OrderStatus
from app.rules.clock import FixedClock
from app.rules.enums import (
    ActionType,
    DecisionOutcome,
    ItemCondition,
    RefundBasis,
    ReturnReason,
)
from app.rules.idempotency import generate_idempotency_key
from app.rules.refunds import (
    RefundInput,
    calculate_refund_limit,
    check_refund_eligibility,
)
from app.rules.returns import ReturnInput, check_return_eligibility
from hypothesis import given
from hypothesis import strategies as st

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
CLOCK = FixedClock(NOW)
TID = uuid.UUID(int=1)
OID = uuid.UUID(int=2)

_pence = st.integers(min_value=0, max_value=1_000_000)
_pos = st.integers(min_value=1, max_value=1_000_000)


@given(item=_pence, paid=_pence, prior=_pence, requested=_pos)
def test_refund_limit_never_exceeds_item_or_remaining(
    item: int, paid: int, prior: int, requested: int
) -> None:
    inp = RefundInput(
        ownership_confirmed=True,
        ticket_id=TID,
        order_id=OID,
        requested_amount_pence=requested,
        item_line_total_pence=item,
        order_total_paid_pence=paid,
        prior_refunded_pence=prior,
        basis=RefundBasis.damaged_item,
    )
    limit = calculate_refund_limit(inp)
    remaining = max(0, paid - prior)
    assert limit <= item
    assert limit <= remaining


@given(item=_pos, paid=_pos, prior_a=_pence, extra=_pence)
def test_more_prior_refunds_cannot_increase_remaining(
    item: int, paid: int, prior_a: int, extra: int
) -> None:
    def limit(prior: int) -> int:
        return calculate_refund_limit(
            RefundInput(
                ownership_confirmed=True,
                ticket_id=TID,
                order_id=OID,
                requested_amount_pence=1,
                item_line_total_pence=item,
                order_total_paid_pence=paid,
                prior_refunded_pence=prior,
                basis=RefundBasis.damaged_item,
            )
        )

    assert limit(prior_a + extra) <= limit(prior_a)


@given(amount=_pence)
def test_idempotency_stable(amount: int) -> None:
    a = generate_idempotency_key(
        ticket_id=TID,
        action_type=ActionType.refund,
        order_id=OID,
        amount_pence=amount + 1,
    )
    b = generate_idempotency_key(
        ticket_id=TID,
        action_type=ActionType.refund,
        order_id=OID,
        amount_pence=amount + 1,
    )
    assert a == b


@given(days_ago=st.integers(min_value=31, max_value=3650))
def test_after_deadline_never_eligible(days_ago: int) -> None:
    result = check_return_eligibility(
        ReturnInput(
            ownership_confirmed=True,
            order_status=OrderStatus.delivered,
            delivered_at=NOW - timedelta(days=days_ago),
            reason=ReturnReason.changed_mind,
            condition=ItemCondition.unused,
        ),
        CLOCK,
    )
    assert result.outcome != DecisionOutcome.eligible


@given(requested=_pos, item=_pence, paid=_pence, prior=_pence)
def test_ownership_mismatch_never_executable(
    requested: int, item: int, paid: int, prior: int
) -> None:
    result = check_refund_eligibility(
        RefundInput(
            ownership_confirmed=False,
            ticket_id=TID,
            order_id=OID,
            requested_amount_pence=requested,
            item_line_total_pence=item,
            order_total_paid_pence=paid,
            prior_refunded_pence=prior,
            basis=RefundBasis.damaged_item,
        )
    )
    assert result.execution_permitted is False
    assert result.outcome == DecisionOutcome.blocked
