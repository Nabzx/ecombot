"""Damaged-item and incorrect-item remedy rules.

Both require the item to have been delivered and reported within 30 calendar days. A
customer's free-text claim (e.g. the SKU they say they received) is treated as evidence,
not as trusted system data. No image analysis is performed. Remedies require Supervisor
approval.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel

from app.rules.clock import Clock
from app.rules.constants import REMEDY_WINDOW_DAYS
from app.rules.enums import DecisionOutcome, ReasonCode, RiskLevel, Route
from app.rules.models import RuleResult

DAMAGED_RULE_VERSION = "damaged-remedy-v1"
INCORRECT_RULE_VERSION = "incorrect-remedy-v1"


class DamagedItemInput(BaseModel):
    ownership_confirmed: bool
    delivered_at: datetime | None
    evidence_sufficient: bool = True


def check_damaged_item_remedy(inp: DamagedItemInput, clock: Clock) -> RuleResult:
    if not inp.ownership_confirmed:
        return _blocked(DAMAGED_RULE_VERSION)
    if inp.delivered_at is None:
        return _needs_info(DAMAGED_RULE_VERSION)

    days_since = (clock.today() - inp.delivered_at.date()).days
    computed = {"days_since_delivery": days_since, "window_days": REMEDY_WINDOW_DAYS}
    if inp.delivered_at.date() + timedelta(days=REMEDY_WINDOW_DAYS) < clock.today():
        return RuleResult(
            outcome=DecisionOutcome.requires_review,
            eligible=False,
            risk_level=RiskLevel.medium,
            route=Route.escalate,
            reason_codes=[ReasonCode.DAMAGED_ITEM_REPORT_OUTSIDE_WINDOW],
            explanations=["Damage reported after the 30-day window; escalate."],
            computed=computed,
            rule_version=DAMAGED_RULE_VERSION,
        )
    if not inp.evidence_sufficient:
        return RuleResult(
            outcome=DecisionOutcome.requires_review,
            eligible=False,
            risk_level=RiskLevel.medium,
            route=Route.escalate,
            reason_codes=[ReasonCode.DAMAGE_EVIDENCE_INSUFFICIENT],
            explanations=["The damage evidence is insufficient; needs review."],
            computed=computed,
            rule_version=DAMAGED_RULE_VERSION,
        )
    return RuleResult(
        outcome=DecisionOutcome.requires_approval,
        eligible=True,
        risk_level=RiskLevel.high,
        route=Route.await_supervisor,
        reason_codes=[
            ReasonCode.DAMAGED_ITEM_REMEDY_ELIGIBLE,
            ReasonCode.REPLACEMENT_SUPERVISOR_APPROVAL_REQUIRED,
        ],
        explanations=[
            "A replacement or refund may be offered for the damaged item.",
            "The remedy requires Supervisor approval.",
        ],
        computed=computed,
        approval_required=True,
        execution_permitted=False,
        rule_version=DAMAGED_RULE_VERSION,
    )


class IncorrectItemInput(BaseModel):
    ownership_confirmed: bool
    delivered_at: datetime | None
    ordered_sku: str
    claimed_received_sku: str | None = None


def check_incorrect_item_remedy(inp: IncorrectItemInput, clock: Clock) -> RuleResult:
    if not inp.ownership_confirmed:
        return _blocked(INCORRECT_RULE_VERSION)
    if inp.delivered_at is None:
        return _needs_info(INCORRECT_RULE_VERSION)

    days_since = (clock.today() - inp.delivered_at.date()).days
    computed = {"days_since_delivery": days_since, "window_days": REMEDY_WINDOW_DAYS}
    if inp.delivered_at.date() + timedelta(days=REMEDY_WINDOW_DAYS) < clock.today():
        return RuleResult(
            outcome=DecisionOutcome.requires_review,
            eligible=False,
            risk_level=RiskLevel.medium,
            route=Route.escalate,
            reason_codes=[ReasonCode.DAMAGED_ITEM_REPORT_OUTSIDE_WINDOW],
            explanations=["Reported after the 30-day window; escalate."],
            computed=computed,
            rule_version=INCORRECT_RULE_VERSION,
        )

    claimed = (inp.claimed_received_sku or "").strip()
    if not claimed:
        return RuleResult(
            outcome=DecisionOutcome.escalate,
            eligible=None,
            risk_level=RiskLevel.medium,
            route=Route.escalate,
            reason_codes=[
                ReasonCode.RECEIVED_SKU_UNKNOWN,
                ReasonCode.ITEM_MISMATCH_UNVERIFIED,
            ],
            explanations=["The received item cannot be identified; escalate."],
            computed=computed,
            rule_version=INCORRECT_RULE_VERSION,
        )
    if claimed.upper() == inp.ordered_sku.strip().upper():
        return RuleResult(
            outcome=DecisionOutcome.ineligible,
            eligible=False,
            risk_level=RiskLevel.read_only,
            route=Route.continue_processing,
            reason_codes=[ReasonCode.RECEIVED_SKU_MATCHES_ORDER],
            explanations=["The received SKU matches the order; no mismatch."],
            computed=computed,
            rule_version=INCORRECT_RULE_VERSION,
        )
    return RuleResult(
        outcome=DecisionOutcome.requires_approval,
        eligible=True,
        risk_level=RiskLevel.high,
        route=Route.await_supervisor,
        reason_codes=[
            ReasonCode.INCORRECT_ITEM_REMEDY_ELIGIBLE,
            ReasonCode.REPLACEMENT_SUPERVISOR_APPROVAL_REQUIRED,
        ],
        explanations=[
            "The claimed SKU differs from the order; a remedy may be offered.",
            "The remedy requires Supervisor approval.",
        ],
        computed=computed,
        approval_required=True,
        execution_permitted=False,
        rule_version=INCORRECT_RULE_VERSION,
    )


def _blocked(version: str) -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.blocked,
        eligible=False,
        risk_level=RiskLevel.blocked,
        route=Route.blocked,
        reason_codes=[ReasonCode.ORDER_OWNERSHIP_MISMATCH],
        explanations=["Ownership is not confirmed; the remedy check is blocked."],
        rule_version=version,
    )


def _needs_info(version: str) -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.needs_information,
        eligible=None,
        risk_level=RiskLevel.read_only,
        route=Route.needs_information,
        reason_codes=[ReasonCode.DELIVERY_DATE_MISSING],
        explanations=["No delivery date is available to assess the window."],
        missing_information=["delivery date"],
        rule_version=version,
    )
