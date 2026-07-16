"""Return-eligibility rules.

Window semantics: **calendar days, inclusive**. Delivery day is day 0; an item is
eligible through ``delivered_at.date() + 30 days``. Day 30 is eligible, day 31 is not.
Comparison uses the injected clock's UTC ``today()`` against the delivery date.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel

from app.models.enums import OrderStatus
from app.rules.clock import Clock
from app.rules.constants import RETURN_WINDOW_DAYS
from app.rules.enums import (
    DecisionOutcome,
    ItemCondition,
    ReasonCode,
    ReturnReason,
    RiskLevel,
    Route,
)
from app.rules.models import RuleResult

RULE_VERSION = "returns-v1"

_EXCEPTION_REASONS = {ReturnReason.damaged, ReturnReason.incorrect_item}


class ReturnInput(BaseModel):
    ownership_confirmed: bool
    order_status: OrderStatus
    delivered_at: datetime | None
    reason: ReturnReason
    condition: ItemCondition = ItemCondition.unknown
    already_returned: bool = False


def check_return_eligibility(inp: ReturnInput, clock: Clock) -> RuleResult:
    if not inp.ownership_confirmed:
        return _blocked()

    if inp.order_status != OrderStatus.delivered:
        return _simple(
            DecisionOutcome.ineligible,
            Route.continue_processing,
            ReasonCode.RETURN_NOT_YET_APPLICABLE,
            "The order is not delivered; use the delivery or cancellation flow.",
        )

    if inp.delivered_at is None:
        return _simple(
            DecisionOutcome.needs_information,
            Route.needs_information,
            ReasonCode.DELIVERY_DATE_MISSING,
            "The delivery date is unknown, so the return window cannot be computed.",
            missing=["delivery date"],
        )

    if inp.already_returned:
        return _simple(
            DecisionOutcome.ineligible,
            Route.continue_processing,
            ReasonCode.ITEM_ALREADY_RETURNED,
            "The item has already been returned.",
        )

    deadline = inp.delivered_at.date() + timedelta(days=RETURN_WINDOW_DAYS)
    today = clock.today()
    days_since = (today - inp.delivered_at.date()).days
    computed = {"days_since_delivery": days_since, "window_days": RETURN_WINDOW_DAYS}

    if today > deadline:
        return _simple(
            DecisionOutcome.ineligible,
            Route.continue_processing,
            ReasonCode.RETURN_WINDOW_EXPIRED,
            f"The 30-day return window closed on {deadline.isoformat()}.",
            computed=computed,
        )

    reason_codes = [ReasonCode.RETURN_WITHIN_WINDOW]
    explanations = ["The item is within the 30-day return window."]

    if inp.reason == ReturnReason.damaged:
        reason_codes.append(ReasonCode.DAMAGED_ITEM_EXCEPTION)
        explanations.append("Damaged items do not require original packaging.")
    elif inp.reason == ReturnReason.incorrect_item:
        reason_codes.append(ReasonCode.INCORRECT_ITEM_EXCEPTION)
        explanations.append("Incorrect items do not require original packaging.")
    elif inp.condition != ItemCondition.unused:
        # changed_mind / not_as_described / other require an unused item.
        return _simple(
            DecisionOutcome.ineligible,
            Route.continue_processing,
            ReasonCode.ITEM_CONDITION_NOT_ELIGIBLE,
            "A change-of-mind return requires the item to be unused.",
            computed=computed,
        )

    # Eligible: a return/RMA is a Supervisor-approved proposal in the frozen MVP.
    return RuleResult(
        outcome=DecisionOutcome.eligible,
        eligible=True,
        risk_level=RiskLevel.medium,
        route=Route.await_supervisor,
        reason_codes=reason_codes,
        explanations=explanations,
        computed=computed,
        approval_required=True,
        execution_permitted=False,
        rule_version=RULE_VERSION,
    )


def _blocked() -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.blocked,
        eligible=False,
        risk_level=RiskLevel.blocked,
        route=Route.blocked,
        reason_codes=[ReasonCode.ORDER_OWNERSHIP_MISMATCH],
        explanations=["Ownership is not confirmed; the return check is blocked."],
        execution_permitted=False,
        rule_version=RULE_VERSION,
    )


def _simple(
    outcome: DecisionOutcome,
    route: Route,
    code: ReasonCode,
    explanation: str,
    *,
    missing: list[str] | None = None,
    computed: dict[str, int] | None = None,
) -> RuleResult:
    return RuleResult(
        outcome=outcome,
        eligible=False,
        risk_level=RiskLevel.read_only,
        route=route,
        reason_codes=[code],
        explanations=[explanation],
        missing_information=missing or [],
        computed=computed or {},
        execution_permitted=False,
        rule_version=RULE_VERSION,
    )
