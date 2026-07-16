"""CLI for inspecting the deterministic rules layer.

python -m app.rules.cli list-rules
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from app.rules import (
    cancellations,
    deliveries,
    ownership,
    policies,
    refunds,
    remedies,
    returns,
    routing,
)


@dataclass(frozen=True, slots=True)
class RuleInfo:
    name: str
    version: str
    description: str
    time_semantics: str


RULES: tuple[RuleInfo, ...] = (
    RuleInfo(
        "ownership", ownership.RULE_VERSION, "Customer/order ownership gate", "n/a"
    ),
    RuleInfo(
        "returns",
        returns.RULE_VERSION,
        "Return eligibility (30-day window)",
        "calendar days, inclusive",
    ),
    RuleInfo(
        "refunds",
        refunds.RULE_VERSION,
        "Refund eligibility, limits and risk bands",
        "n/a",
    ),
    RuleInfo(
        "cancellations", cancellations.RULE_VERSION, "Cancellation eligibility", "n/a"
    ),
    RuleInfo(
        "delivery_delay",
        deliveries.DELAY_RULE_VERSION,
        "Delivery-delay tiers",
        "calendar days",
    ),
    RuleInfo(
        "missing_delivery",
        deliveries.MISSING_RULE_VERSION,
        "Missing-delivery handling",
        "calendar days",
    ),
    RuleInfo(
        "damaged_remedy",
        remedies.DAMAGED_RULE_VERSION,
        "Damaged-item remedy",
        "calendar days",
    ),
    RuleInfo(
        "incorrect_remedy",
        remedies.INCORRECT_RULE_VERSION,
        "Incorrect-item remedy",
        "calendar days",
    ),
    RuleInfo(
        "policy",
        policies.RULE_VERSION,
        "Policy validity and conflict detection",
        "calendar date",
    ),
    RuleInfo("routing", routing.RULE_VERSION, "Confidence, risk and routing", "n/a"),
)


def _list_rules() -> int:
    print(f"{'RULE':<18}{'VERSION':<22}{'TIME SEMANTICS':<26}DESCRIPTION")
    for rule in RULES:
        print(
            f"{rule.name:<18}{rule.version:<22}{rule.time_semantics:<26}"
            f"{rule.description}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rules", description="AgentOps rules tooling")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-rules", help="List deterministic rules and versions")
    args = parser.parse_args(argv)
    if args.command == "list-rules":
        return _list_rules()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
