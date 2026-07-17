"""Outbox job status enum (S6)."""

from __future__ import annotations

from enum import StrEnum


class OutboxStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


# A job in one of these statuses is never claimed by a worker.
UNCLAIMABLE_STATUSES: frozenset[OutboxStatus] = frozenset(
    {
        OutboxStatus.SUCCEEDED,
        OutboxStatus.DEAD_LETTER,
        OutboxStatus.CANCELLED,
        OutboxStatus.FAILED,
    }
)

CLAIMABLE_STATUSES: frozenset[OutboxStatus] = frozenset(
    {OutboxStatus.PENDING, OutboxStatus.RETRY_SCHEDULED}
)
