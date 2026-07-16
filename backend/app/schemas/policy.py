"""Policy schemas."""

from __future__ import annotations

import uuid
from datetime import date

from app.models.enums import PolicyStatus
from app.schemas.common import ORMModel


class PolicySummary(ORMModel):
    id: uuid.UUID
    topic: str
    title: str
    description: str


class PolicyVersionDetail(ORMModel):
    id: uuid.UUID
    policy_id: uuid.UUID
    version: int
    status: PolicyStatus
    body: str
    effective_from: date
    effective_to: date | None


class PolicyDetail(PolicySummary):
    versions: list[PolicyVersionDetail]
