"""Policy validity/conflict and ownership rule tests (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from app.models.enums import PolicyStatus
from app.rules.clock import FixedClock
from app.rules.enums import DecisionOutcome, ReasonCode
from app.rules.ownership import OwnershipInput, check_ownership
from app.rules.policies import PolicyVersionFact, validate_policy_versions

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
CLOCK = FixedClock(NOW)
TODAY = NOW.date()


def _fact(
    status: PolicyStatus, frm: date, to: date | None, version: int = 1
) -> PolicyVersionFact:
    return PolicyVersionFact(
        policy_id=uuid.uuid4(),
        policy_version_id=uuid.uuid4(),
        topic="returns",
        version=version,
        status=status,
        effective_from=frm,
        effective_to=to,
    )


def test_single_active_policy_authorises() -> None:
    result = validate_policy_versions(
        "returns",
        [_fact(PolicyStatus.active, TODAY - timedelta(days=100), None)],
        CLOCK,
    )
    assert result.outcome == DecisionOutcome.eligible
    assert result.has(ReasonCode.POLICY_ACTIVE)
    assert len(result.policy_evidence) == 1


def test_no_versions_not_found() -> None:
    result = validate_policy_versions("returns", [], CLOCK)
    assert result.has(ReasonCode.POLICY_NOT_FOUND)


def test_conflicting_active_versions_escalate() -> None:
    facts = [
        _fact(PolicyStatus.active, TODAY - timedelta(days=100), None, version=1),
        _fact(PolicyStatus.active, TODAY - timedelta(days=50), None, version=2),
    ]
    result = validate_policy_versions("returns", facts, CLOCK)
    assert result.outcome == DecisionOutcome.escalate
    assert result.has(ReasonCode.POLICY_CONFLICT)


def test_expired_policy_cannot_authorise() -> None:
    result = validate_policy_versions(
        "returns",
        [
            _fact(
                PolicyStatus.expired,
                TODAY - timedelta(days=400),
                TODAY - timedelta(days=300),
            )
        ],
        CLOCK,
    )
    assert result.has(ReasonCode.POLICY_EXPIRED)
    assert result.has(ReasonCode.POLICY_CANNOT_AUTHORISE_ACTION)


def test_superseded_policy_cannot_authorise() -> None:
    result = validate_policy_versions(
        "returns",
        [
            _fact(
                PolicyStatus.superseded,
                TODAY - timedelta(days=400),
                TODAY - timedelta(days=10),
            )
        ],
        CLOCK,
    )
    assert result.has(ReasonCode.POLICY_SUPERSEDED)


def test_future_policy_not_effective_yet() -> None:
    result = validate_policy_versions(
        "returns", [_fact(PolicyStatus.active, TODAY + timedelta(days=10), None)], CLOCK
    )
    assert result.has(ReasonCode.POLICY_NOT_EFFECTIVE_YET)


# --- ownership ---------------------------------------------------------------

CUST = uuid.uuid4()
ORDER = uuid.uuid4()


def test_ownership_confirmed() -> None:
    result = check_ownership(
        OwnershipInput(
            resolved_customer_id=CUST,
            customer_match_count=1,
            resolved_order_id=ORDER,
            order_customer_id=CUST,
            order_match_count=1,
        )
    )
    assert result.outcome == DecisionOutcome.eligible
    assert result.has(ReasonCode.ORDER_OWNERSHIP_CONFIRMED)


def test_ownership_cross_customer_blocked() -> None:
    result = check_ownership(
        OwnershipInput(
            resolved_customer_id=CUST,
            customer_match_count=1,
            resolved_order_id=ORDER,
            order_customer_id=uuid.uuid4(),
            order_match_count=1,
        )
    )
    assert result.outcome == DecisionOutcome.blocked
    assert result.has(ReasonCode.CROSS_CUSTOMER_ACCESS_BLOCKED)
    assert result.execution_permitted is False


def test_ownership_ambiguous_customer_escalates() -> None:
    result = check_ownership(
        OwnershipInput(resolved_customer_id=None, customer_match_count=2)
    )
    assert result.outcome == DecisionOutcome.escalate
    assert result.has(ReasonCode.CUSTOMER_MATCH_AMBIGUOUS)


def test_ownership_no_customer_needs_information() -> None:
    result = check_ownership(
        OwnershipInput(resolved_customer_id=None, customer_match_count=0)
    )
    assert result.outcome == DecisionOutcome.needs_information
    assert result.has(ReasonCode.CUSTOMER_NOT_IDENTIFIED)


def test_ownership_no_order_needs_information() -> None:
    result = check_ownership(
        OwnershipInput(resolved_customer_id=CUST, customer_match_count=1)
    )
    assert result.has(ReasonCode.ORDER_NOT_IDENTIFIED)
