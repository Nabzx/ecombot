"""Typed result structures for deterministic decisions.

A ``RuleResult`` is a plain, serialisable snapshot of a decision: outcome, stable reason
codes, risk, route, computed values and evidence. It never contains ORM entities,
secrets or unnecessary PII, and every rule stamps a ``rule_version``.
"""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import PolicyStatus
from app.rules.enums import (
    ApprovalRole,
    DecisionOutcome,
    ReasonCode,
    RiskLevel,
    Route,
)


class PolicyEvidence(BaseModel):
    """A reference to the policy version relied upon (no full body)."""

    model_config = ConfigDict(from_attributes=True)

    policy_id: uuid.UUID
    policy_version_id: uuid.UUID
    topic: str
    version: int
    status: PolicyStatus
    effective_from: date
    effective_to: date | None


class RuleResult(BaseModel):
    """The outcome of a deterministic rule evaluation."""

    outcome: DecisionOutcome
    eligible: bool | None = None
    risk_level: RiskLevel = RiskLevel.read_only
    route: Route
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    explanations: list[str] = Field(default_factory=list)
    computed: dict[str, int] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    policy_evidence: list[PolicyEvidence] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    approval_required: bool = False
    execution_permitted: bool = False
    rule_version: str
    idempotency_key: str | None = None
    # Populated by the routing rule.
    required_role: ApprovalRole | None = None
    may_propose: bool | None = None

    def has(self, code: ReasonCode) -> bool:
        return code in self.reason_codes
