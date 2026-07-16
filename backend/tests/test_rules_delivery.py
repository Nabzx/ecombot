"""Delivery-delay tier and missing-delivery boundary tests (no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.models.enums import OrderStatus, ShipmentStatus
from app.rules.clock import FixedClock
from app.rules.deliveries import (
    DeliveryDelayInput,
    MissingDeliveryInput,
    check_missing_delivery,
    classify_delivery_delay,
)
from app.rules.enums import DecisionOutcome, ReasonCode

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
CLOCK = FixedClock(NOW)
TODAY = NOW.date()


def _delay(days_late: int) -> DeliveryDelayInput:
    return DeliveryDelayInput(
        order_status=OrderStatus.shipped,
        shipment_present=True,
        shipment_status=ShipmentStatus.in_transit,
        promised_delivery_date=TODAY - timedelta(days=days_late),
    )


@pytest.mark.parametrize(
    ("days_late", "code"),
    [
        (0, ReasonCode.DELIVERY_ON_TIME),
        (1, ReasonCode.DELIVERY_MINOR_DELAY),
        (3, ReasonCode.DELIVERY_MINOR_DELAY),
        (4, ReasonCode.DELIVERY_SIGNIFICANT_DELAY),
        (9, ReasonCode.DELIVERY_SIGNIFICANT_DELAY),
        (10, ReasonCode.DELIVERY_SEVERE_DELAY),
    ],
)
def test_delivery_delay_tiers(days_late: int, code: ReasonCode) -> None:
    assert classify_delivery_delay(_delay(days_late), CLOCK).has(code)


def test_delivery_severe_delay_escalates() -> None:
    assert (
        classify_delivery_delay(_delay(10), CLOCK).outcome == DecisionOutcome.escalate
    )


def test_delivery_missing_promised_date() -> None:
    inp = _delay(0).model_copy(update={"promised_delivery_date": None})
    result = classify_delivery_delay(inp, CLOCK)
    assert result.has(ReasonCode.DELIVERY_DATE_MISSING)


def test_delivery_not_shipped_and_delivered() -> None:
    not_shipped = classify_delivery_delay(
        DeliveryDelayInput(
            order_status=OrderStatus.processing,
            shipment_present=False,
            shipment_status=None,
            promised_delivery_date=None,
        ),
        CLOCK,
    )
    assert not_shipped.has(ReasonCode.ORDER_NOT_SHIPPED)
    delivered = classify_delivery_delay(
        _delay(0).model_copy(update={"shipment_status": ShipmentStatus.delivered}),
        CLOCK,
    )
    assert delivered.has(ReasonCode.DELIVERY_ALREADY_COMPLETED)


def _missing(status: ShipmentStatus | None, **kw: object) -> MissingDeliveryInput:
    return MissingDeliveryInput(
        ownership_confirmed=bool(kw.get("ownership_confirmed", True)),
        shipment_present=bool(kw.get("shipment_present", True)),
        shipment_status=status,
        promised_delivery_date=kw.get("promised_delivery_date"),
        customer_disputes_receipt=bool(kw.get("customer_disputes_receipt", True)),
    )


def test_missing_delivered_but_disputed_escalates() -> None:
    result = check_missing_delivery(_missing(ShipmentStatus.delivered), CLOCK)
    assert result.outcome == DecisionOutcome.escalate
    assert result.has(ReasonCode.DELIVERED_BUT_DISPUTED)
    assert result.has(ReasonCode.MISSING_DELIVERY_ESCALATION_REQUIRED)


def test_missing_lost_is_remedy_eligible() -> None:
    result = check_missing_delivery(_missing(ShipmentStatus.lost), CLOCK)
    assert result.outcome == DecisionOutcome.requires_approval
    assert result.has(ReasonCode.LOST_SHIPMENT_REMEDY_ELIGIBLE)
    assert result.approval_required is True


def test_missing_exception_requires_review() -> None:
    result = check_missing_delivery(_missing(ShipmentStatus.exception), CLOCK)
    assert result.has(ReasonCode.SHIPMENT_EXCEPTION_REQUIRES_REVIEW)


def test_missing_in_transit_shares_tracking() -> None:
    result = check_missing_delivery(_missing(ShipmentStatus.in_transit), CLOCK)
    assert result.has(ReasonCode.SHIPMENT_STILL_IN_TRANSIT)


def test_missing_data_needs_information() -> None:
    result = check_missing_delivery(_missing(None, shipment_present=False), CLOCK)
    assert result.outcome == DecisionOutcome.needs_information
    assert result.has(ReasonCode.SHIPMENT_DATA_MISSING)


def test_missing_ownership_blocks() -> None:
    result = check_missing_delivery(
        _missing(ShipmentStatus.lost, ownership_confirmed=False), CLOCK
    )
    assert result.outcome == DecisionOutcome.blocked
