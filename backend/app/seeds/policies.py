"""Policy seed definitions.

Core policy bodies are read from ``data/policies/*.md``. On top of the current active
versions this adds:

- a **superseded** earlier returns policy (the old 14-day rule),
- an **expired** promotional policy, and
- a controlled **conflict fixture** (two overlapping active versions) whose topic is
  clearly marked so the integrity check tolerates it while forbidding conflicts anywhere
  else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.core.paths import get_policies_dir
from app.models.enums import PolicyStatus

# Topic used only for the deliberate conflicting-policy test fixture.
CONFLICT_FIXTURE_TOPIC = "fixture_conflicting_returns"


@dataclass(frozen=True, slots=True)
class VersionDef:
    version: int
    status: PolicyStatus
    body: str
    effective_from: date
    effective_to: date | None


@dataclass(frozen=True, slots=True)
class PolicyDef:
    topic: str
    title: str
    description: str
    versions: tuple[VersionDef, ...]


_CORE = (
    ("returns", "Returns Policy", "How and when customers can return items."),
    ("refunds", "Refunds Policy", "When and how refunds are issued."),
    ("cancellations", "Cancellations Policy", "When an order can be cancelled."),
    ("delivery_delays", "Delivery Delays Policy", "How delivery delays are handled."),
    ("missing_deliveries", "Missing Deliveries Policy", "Handling non-receipt claims."),
    ("damaged_items", "Damaged Items Policy", "Remedies for items damaged on arrival."),
    ("incorrect_items", "Incorrect Items Policy", "Remedies for wrong items received."),
    (
        "privacy_verification",
        "Privacy and Account Verification Policy",
        "Identity verification and data-sharing rules.",
    ),
)

_OLD_RETURNS_BODY = (
    "# Returns Policy (superseded)\n\n"
    "Items may be returned within **14 days of delivery** in unused condition. This "
    "earlier policy was replaced by the current 30-day returns policy.\n"
)

_EXPIRED_PROMO_BODY = (
    "# Extended Christmas Returns (expired)\n\n"
    "Orders placed in the promotional period could be returned until 31 January. This "
    "seasonal policy is no longer in effect.\n"
)

_CONFLICT_BODY_A = (
    "# Conflict Fixture A\n\n"
    "Returns are accepted within **30 days** of delivery. (Deliberate test fixture.)\n"
)
_CONFLICT_BODY_B = (
    "# Conflict Fixture B\n\n"
    "Returns are accepted within **45 days** of delivery. (Deliberate test fixture "
    "that conflicts with fixture A.)\n"
)


def _read_body(topic: str) -> str:
    return (get_policies_dir() / f"{topic}.md").read_text(encoding="utf-8")


def build_policy_defs(reference_date: date) -> list[PolicyDef]:
    """Build all policy definitions anchored to ``reference_date``."""
    long_ago = reference_date - timedelta(days=365)
    older = reference_date - timedelta(days=730)
    defs: list[PolicyDef] = []

    for topic, title, description in _CORE:
        versions: tuple[VersionDef, ...]
        if topic == "returns":
            versions = (
                VersionDef(
                    1,
                    PolicyStatus.superseded,
                    _OLD_RETURNS_BODY,
                    older,
                    long_ago,
                ),
                VersionDef(2, PolicyStatus.active, _read_body(topic), long_ago, None),
            )
        else:
            versions = (
                VersionDef(1, PolicyStatus.active, _read_body(topic), long_ago, None),
            )
        defs.append(PolicyDef(topic, title, description, versions))

    # Expired seasonal promotion.
    defs.append(
        PolicyDef(
            "seasonal_promotions",
            "Extended Christmas Returns",
            "Expired seasonal returns extension.",
            (
                VersionDef(
                    1,
                    PolicyStatus.expired,
                    _EXPIRED_PROMO_BODY,
                    reference_date - timedelta(days=400),
                    reference_date - timedelta(days=350),
                ),
            ),
        )
    )

    # Deliberate conflict fixture: two overlapping active versions.
    defs.append(
        PolicyDef(
            CONFLICT_FIXTURE_TOPIC,
            "Conflicting Returns (test fixture)",
            "Deliberately conflicting active versions for later conflict-detection tests.",
            (
                VersionDef(
                    1,
                    PolicyStatus.active,
                    _CONFLICT_BODY_A,
                    reference_date - timedelta(days=100),
                    None,
                ),
                VersionDef(
                    2,
                    PolicyStatus.active,
                    _CONFLICT_BODY_B,
                    reference_date - timedelta(days=50),
                    None,
                ),
            ),
        )
    )

    return defs
