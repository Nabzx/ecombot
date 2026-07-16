"""Output schemas for tools not returning a domain schema or RuleResult directly."""

from __future__ import annotations

from pydantic import BaseModel

from app.rules.models import PolicyEvidence
from app.schemas.customer import CustomerSummary
from app.schemas.order import OrderSummary


class CustomerSearchResult(BaseModel):
    match_count: int
    matches: list[CustomerSummary]


class OrderSearchResult(BaseModel):
    match_count: int
    orders: list[OrderSummary]


class RefundLimitResult(BaseModel):
    maximum_refund_pence: int
    item_line_total_pence: int
    remaining_order_balance_pence: int


class ActivePolicyResult(BaseModel):
    topic: str
    evidence: PolicyEvidence


class IdempotencyKeyResult(BaseModel):
    idempotency_key: str
