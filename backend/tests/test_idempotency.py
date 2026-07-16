"""Idempotency-key tests: stability, distinctions, sentinel and normalisation."""

from __future__ import annotations

import uuid

import pytest
from app.rules.enums import ActionType
from app.rules.idempotency import NO_AMOUNT_SENTINEL, generate_idempotency_key

TID = uuid.UUID(int=1)
OID = uuid.UUID(int=2)


def test_stable_for_identical_inputs() -> None:
    a = generate_idempotency_key(
        ticket_id=TID, action_type=ActionType.refund, order_id=OID, amount_pence=5000
    )
    b = generate_idempotency_key(
        ticket_id=TID, action_type=ActionType.refund, order_id=OID, amount_pence=5000
    )
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_one_penny_difference_changes_key() -> None:
    a = generate_idempotency_key(
        ticket_id=TID, action_type=ActionType.refund, order_id=OID, amount_pence=5000
    )
    b = generate_idempotency_key(
        ticket_id=TID, action_type=ActionType.refund, order_id=OID, amount_pence=5001
    )
    assert a != b


def test_action_type_distinguishes() -> None:
    refund = generate_idempotency_key(
        ticket_id=TID, action_type=ActionType.refund, order_id=OID, amount_pence=5000
    )
    other = generate_idempotency_key(
        ticket_id=TID,
        action_type=ActionType.replacement,
        order_id=OID,
        amount_pence=5000,
    )
    assert refund != other


def test_action_type_normalisation_matches_enum() -> None:
    from_enum = generate_idempotency_key(
        ticket_id=TID, action_type=ActionType.cancellation, order_id=OID
    )
    from_str = generate_idempotency_key(
        ticket_id=TID, action_type="  Cancellation  ", order_id=OID
    )
    assert from_enum == from_str


def test_uuid_formatting_is_case_insensitive() -> None:
    lower = generate_idempotency_key(
        ticket_id=str(TID).lower(), action_type=ActionType.cancellation, order_id=OID
    )
    upper = generate_idempotency_key(
        ticket_id=str(TID).upper(), action_type=ActionType.cancellation, order_id=OID
    )
    assert lower == upper


def test_none_amount_uses_sentinel_and_refund_requires_amount() -> None:
    # A non-amount action accepts None.
    key = generate_idempotency_key(
        ticket_id=TID, action_type=ActionType.cancellation, order_id=OID
    )
    assert isinstance(key, str)
    assert NO_AMOUNT_SENTINEL == "none"
    # A refund without an amount is a programming error.
    with pytest.raises(ValueError, match="amount_pence is required"):
        generate_idempotency_key(
            ticket_id=TID, action_type=ActionType.refund, order_id=OID
        )
