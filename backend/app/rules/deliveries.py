"""Delivery-delay classification and missing-delivery handling.

``days_late`` is a **calendar-date** difference: ``clock.today() -
promised_delivery_date`` in whole days. Tiers: <=0 on time, 1-3 minor, 4-9 significant,
>=10 severe (escalate).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.models.enums import OrderStatus, ShipmentStatus
from app.rules.clock import Clock
from app.rules.constants import DELAY_MINOR_MAX_DAYS, DELAY_SIGNIFICANT_MAX_DAYS
from app.rules.enums import DecisionOutcome, ReasonCode, RiskLevel, Route
from app.rules.models import RuleResult

DELAY_RULE_VERSION = "delivery-delay-v1"
MISSING_RULE_VERSION = "missing-delivery-v1"

_IN_TRANSIT = {ShipmentStatus.in_transit, ShipmentStatus.out_for_delivery}
_SEVERE_DELAY_DAYS = DELAY_SIGNIFICANT_MAX_DAYS + 1  # >= 10


class DeliveryDelayInput(BaseModel):
    order_status: OrderStatus
    shipment_present: bool = True
    shipment_status: ShipmentStatus | None
    promised_delivery_date: date | None


def classify_delivery_delay(inp: DeliveryDelayInput, clock: Clock) -> RuleResult:
    if inp.shipment_status == ShipmentStatus.delivered:
        return _info(
            DELAY_RULE_VERSION,
            ReasonCode.DELIVERY_ALREADY_COMPLETED,
            "The order has already been delivered.",
        )
    if inp.shipment_status == ShipmentStatus.lost:
        return _escalate(
            DELAY_RULE_VERSION,
            ReasonCode.SHIPMENT_REPORTED_LOST,
            "The carrier reports the shipment as lost.",
        )
    if inp.shipment_status == ShipmentStatus.exception:
        return _review(
            DELAY_RULE_VERSION,
            ReasonCode.SHIPMENT_EXCEPTION,
            "The shipment is in an exception state and needs review.",
        )
    if (
        not inp.shipment_present
        or inp.shipment_status is None
        or inp.shipment_status == ShipmentStatus.label_created
    ):
        return _info(
            DELAY_RULE_VERSION,
            ReasonCode.ORDER_NOT_SHIPPED,
            "The order has not yet shipped.",
        )
    if inp.promised_delivery_date is None:
        return _needs_info(
            DELAY_RULE_VERSION,
            ReasonCode.DELIVERY_DATE_MISSING,
            "No promised delivery date is available.",
            missing=["promised delivery date"],
        )

    days_late = (clock.today() - inp.promised_delivery_date).days
    computed = {"days_late": days_late}
    if days_late <= 0:
        return _info(
            DELAY_RULE_VERSION,
            ReasonCode.DELIVERY_ON_TIME,
            "The delivery is on time.",
            computed=computed,
        )
    if days_late <= DELAY_MINOR_MAX_DAYS:
        return _tiered(
            ReasonCode.DELIVERY_MINOR_DELAY,
            RiskLevel.low,
            Route.continue_processing,
            "Minor delay; apologise and share tracking.",
            computed,
        )
    if days_late <= DELAY_SIGNIFICANT_MAX_DAYS:
        return _tiered(
            ReasonCode.DELIVERY_SIGNIFICANT_DELAY,
            RiskLevel.medium,
            Route.await_agent,
            "Significant delay; share tracking and explain next options.",
            computed,
        )
    return _escalate(
        DELAY_RULE_VERSION,
        ReasonCode.DELIVERY_SEVERE_DELAY,
        "Severe delay; escalate as a potentially lost shipment.",
        computed=computed,
    )


class MissingDeliveryInput(BaseModel):
    ownership_confirmed: bool
    shipment_present: bool = True
    shipment_status: ShipmentStatus | None
    promised_delivery_date: date | None = None
    customer_disputes_receipt: bool = True


def check_missing_delivery(inp: MissingDeliveryInput, clock: Clock) -> RuleResult:
    if not inp.ownership_confirmed:
        return _blocked(MISSING_RULE_VERSION)

    if not inp.shipment_present or inp.shipment_status is None:
        return _needs_info(
            MISSING_RULE_VERSION,
            ReasonCode.SHIPMENT_DATA_MISSING,
            "No shipment data is available to assess the claim.",
            missing=["shipment data"],
        )

    if (
        inp.shipment_status == ShipmentStatus.delivered
        and inp.customer_disputes_receipt
    ):
        return _escalate(
            MISSING_RULE_VERSION,
            ReasonCode.DELIVERED_BUT_DISPUTED,
            "Carrier shows delivered but the customer disputes receipt; escalate.",
            extra=[ReasonCode.MISSING_DELIVERY_ESCALATION_REQUIRED],
        )
    if inp.shipment_status == ShipmentStatus.lost:
        return RuleResult(
            outcome=DecisionOutcome.requires_approval,
            eligible=True,
            risk_level=RiskLevel.high,
            route=Route.await_supervisor,
            reason_codes=[
                ReasonCode.SHIPMENT_CONFIRMED_LOST,
                ReasonCode.LOST_SHIPMENT_REMEDY_ELIGIBLE,
            ],
            explanations=["Confirmed lost; a resend or refund may be proposed."],
            approval_required=True,
            execution_permitted=False,
            rule_version=MISSING_RULE_VERSION,
        )
    if inp.shipment_status == ShipmentStatus.exception:
        return _review(
            MISSING_RULE_VERSION,
            ReasonCode.SHIPMENT_EXCEPTION_REQUIRES_REVIEW,
            "Shipment exception without a safe resolution; escalate for review.",
        )

    # Still moving. Escalate only if severely late.
    if inp.shipment_status in _IN_TRANSIT and inp.promised_delivery_date is not None:
        days_late = (clock.today() - inp.promised_delivery_date).days
        if days_late >= _SEVERE_DELAY_DAYS:
            return _escalate(
                MISSING_RULE_VERSION,
                ReasonCode.DELIVERY_SEVERE_DELAY,
                "Shipment is severely late; escalate.",
                computed={"days_late": days_late},
            )
    return _info(
        MISSING_RULE_VERSION,
        ReasonCode.SHIPMENT_STILL_IN_TRANSIT,
        "The shipment is still in transit; share tracking, no remedy yet.",
    )


# --- result helpers ----------------------------------------------------------


def _info(
    version: str,
    code: ReasonCode,
    explanation: str,
    *,
    computed: dict[str, int] | None = None,
) -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.ineligible,
        eligible=None,
        risk_level=RiskLevel.read_only,
        route=Route.continue_processing,
        reason_codes=[code],
        explanations=[explanation],
        computed=computed or {},
        rule_version=version,
    )


def _tiered(
    code: ReasonCode,
    risk: RiskLevel,
    route: Route,
    explanation: str,
    computed: dict[str, int],
) -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.requires_review
        if route == Route.await_agent
        else DecisionOutcome.ineligible,
        eligible=None,
        risk_level=risk,
        route=route,
        reason_codes=[code],
        explanations=[explanation],
        computed=computed,
        rule_version=DELAY_RULE_VERSION,
    )


def _review(version: str, code: ReasonCode, explanation: str) -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.requires_review,
        eligible=None,
        risk_level=RiskLevel.medium,
        route=Route.escalate,
        reason_codes=[code],
        explanations=[explanation],
        rule_version=version,
    )


def _escalate(
    version: str,
    code: ReasonCode,
    explanation: str,
    *,
    extra: list[ReasonCode] | None = None,
    computed: dict[str, int] | None = None,
) -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.escalate,
        eligible=None,
        risk_level=RiskLevel.medium,
        route=Route.escalate,
        reason_codes=[code, *(extra or [])],
        explanations=[explanation],
        computed=computed or {},
        rule_version=version,
    )


def _needs_info(
    version: str, code: ReasonCode, explanation: str, *, missing: list[str]
) -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.needs_information,
        eligible=None,
        risk_level=RiskLevel.read_only,
        route=Route.needs_information,
        reason_codes=[code],
        explanations=[explanation],
        missing_information=missing,
        rule_version=version,
    )


def _blocked(version: str) -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.blocked,
        eligible=False,
        risk_level=RiskLevel.blocked,
        route=Route.blocked,
        reason_codes=[ReasonCode.ORDER_OWNERSHIP_MISMATCH],
        explanations=["Ownership is not confirmed; the check is blocked."],
        rule_version=version,
    )
