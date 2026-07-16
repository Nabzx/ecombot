"""Deterministic policy-version validity and conflict detection.

No semantic retrieval here (that is S3). Given the policy versions for a topic and the
current date, decide whether exactly one active version authorises an action. Expired,
superseded and not-yet-effective versions cannot authorise; multiple overlapping active
versions are a conflict and are never silently resolved.
"""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel

from app.models.enums import PolicyStatus
from app.rules.clock import Clock
from app.rules.enums import DecisionOutcome, ReasonCode, RiskLevel, Route
from app.rules.models import PolicyEvidence, RuleResult

RULE_VERSION = "policy-v1"


class PolicyVersionFact(BaseModel):
    policy_id: uuid.UUID
    policy_version_id: uuid.UUID
    topic: str
    version: int
    status: PolicyStatus
    effective_from: date
    effective_to: date | None

    def is_effective_on(self, on_date: date) -> bool:
        if self.effective_from > on_date:
            return False
        return self.effective_to is None or on_date <= self.effective_to

    def to_evidence(self) -> PolicyEvidence:
        return PolicyEvidence(
            policy_id=self.policy_id,
            policy_version_id=self.policy_version_id,
            topic=self.topic,
            version=self.version,
            status=self.status,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
        )


def validate_policy_versions(
    topic: str, versions: list[PolicyVersionFact], clock: Clock
) -> RuleResult:
    today = clock.today()

    if not versions:
        return _escalate(
            [ReasonCode.POLICY_NOT_FOUND, ReasonCode.POLICY_CANNOT_AUTHORISE_ACTION],
            f"No policy exists for topic '{topic}'.",
        )

    active_effective = [
        v
        for v in versions
        if v.status == PolicyStatus.active and v.is_effective_on(today)
    ]

    if len(active_effective) == 1:
        chosen = active_effective[0]
        return RuleResult(
            outcome=DecisionOutcome.eligible,
            eligible=True,
            risk_level=RiskLevel.read_only,
            route=Route.continue_processing,
            reason_codes=[ReasonCode.POLICY_ACTIVE],
            explanations=[f"An active policy authorises actions for '{topic}'."],
            policy_evidence=[chosen.to_evidence()],
            rule_version=RULE_VERSION,
        )

    if len(active_effective) > 1:
        return _escalate(
            [ReasonCode.POLICY_CONFLICT, ReasonCode.POLICY_CANNOT_AUTHORISE_ACTION],
            f"Multiple active policy versions conflict for '{topic}'.",
            evidence=[v.to_evidence() for v in active_effective],
        )

    # No usable active version — explain why with the most specific code available.
    active_versions = [v for v in versions if v.status == PolicyStatus.active]
    if any(v.effective_from > today for v in active_versions):
        code = ReasonCode.POLICY_NOT_EFFECTIVE_YET
        message = f"The policy for '{topic}' is not yet effective."
    elif any(v.status == PolicyStatus.expired for v in versions):
        code = ReasonCode.POLICY_EXPIRED
        message = f"The policy for '{topic}' has expired."
    elif any(v.status == PolicyStatus.superseded for v in versions):
        code = ReasonCode.POLICY_SUPERSEDED
        message = f"The policy for '{topic}' has been superseded."
    else:
        code = ReasonCode.POLICY_NOT_FOUND
        message = f"No active policy authorises actions for '{topic}'."
    return _escalate([code, ReasonCode.POLICY_CANNOT_AUTHORISE_ACTION], message)


def _escalate(
    codes: list[ReasonCode],
    explanation: str,
    *,
    evidence: list[PolicyEvidence] | None = None,
) -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.escalate,
        eligible=False,
        risk_level=RiskLevel.read_only,
        route=Route.escalate,
        reason_codes=codes,
        explanations=[explanation],
        policy_evidence=evidence or [],
        rule_version=RULE_VERSION,
    )
