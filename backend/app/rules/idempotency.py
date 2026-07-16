"""Deterministic idempotency-key generation for future write actions.

Frozen format::

    sha256(ticket_id | action_type | order_id | amount_pence)

The canonical string uses ``|`` separators, lowercased UUIDs, a normalised action type,
and an explicit ``none`` sentinel when no amount applies. It contains no timestamps or
model-generated text, so the same logical action always yields the same key. Later
executed-action and outbox stages can use this unchanged; no duplicate check happens
here.
"""

from __future__ import annotations

import hashlib
import uuid

from app.rules.enums import ActionType

SEPARATOR = "|"
NO_AMOUNT_SENTINEL = "none"

# Actions for which an amount is a required part of the key.
_AMOUNT_REQUIRED = {ActionType.refund}


def normalise_action_type(action_type: ActionType | str) -> str:
    value = (
        action_type.value if isinstance(action_type, ActionType) else str(action_type)
    )
    return value.strip().lower()


def _format_uuid(value: uuid.UUID | str) -> str:
    return str(value).strip().lower()


def _format_amount(action_type: ActionType | str, amount_pence: int | None) -> str:
    normalised = normalise_action_type(action_type)
    if amount_pence is None:
        if normalised in {a.value for a in _AMOUNT_REQUIRED}:
            raise ValueError(f"amount_pence is required for action '{normalised}'")
        return NO_AMOUNT_SENTINEL
    return str(int(amount_pence))


def generate_idempotency_key(
    *,
    ticket_id: uuid.UUID | str,
    action_type: ActionType | str,
    order_id: uuid.UUID | str,
    amount_pence: int | None = None,
) -> str:
    """Return the SHA-256 idempotency key for a proposed action."""
    canonical = SEPARATOR.join(
        [
            _format_uuid(ticket_id),
            normalise_action_type(action_type),
            _format_uuid(order_id),
            _format_amount(action_type, amount_pence),
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
