"""Outbox payload hashing/versioning and retry-backoff unit tests (S6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.actions.enums import ExecutionActionType
from app.outbox.payload import (
    OUTBOX_PAYLOAD_VERSION,
    OutboxJobData,
    PayloadError,
    load_payload,
)
from app.outbox.retry import compute_backoff_seconds


def _payload() -> OutboxJobData:
    return OutboxJobData(
        outbox_job_id=uuid.uuid4(),
        approval_request_id=uuid.uuid4(),
        approval_decision_id=uuid.uuid4(),
        proposed_action_id=uuid.uuid4(),
        workflow_run_id=uuid.uuid4(),
        workflow_name="support-ticket-v2",
        workflow_version="2.0.0",
        ticket_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        order_id=uuid.uuid4(),
        action_type=ExecutionActionType.SIMULATED_REFUND,
        approved_amount_pence=5900,
        business_idempotency_key="act-abc123",
        approval_snapshot_hash="a" * 64,
        rule_result_hash="b" * 64,
        evidence_snapshot_hash="c" * 64,
        created_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
    )


def test_payload_hash_is_stable() -> None:
    payload = _payload()
    assert payload.compute_hash() == payload.compute_hash()


def test_load_payload_round_trips_and_verifies() -> None:
    payload = _payload()
    stored = payload.model_dump(mode="json")
    digest = payload.compute_hash()
    loaded = load_payload(stored, digest)
    assert loaded.action_type == ExecutionActionType.SIMULATED_REFUND
    assert loaded.approved_amount_pence == 5900


def test_tampered_payload_is_rejected() -> None:
    payload = _payload()
    digest = payload.compute_hash()
    tampered = payload.model_dump(mode="json")
    tampered["approved_amount_pence"] = 999_999
    with pytest.raises(PayloadError, match="tampered"):
        load_payload(tampered, digest)


def test_unsupported_version_fails_safe() -> None:
    payload = _payload()
    stored = payload.model_dump(mode="json")
    stored["payload_version"] = "outbox-payload-v99"
    with pytest.raises(PayloadError, match="unsupported"):
        load_payload(stored, payload.compute_hash())


def test_payload_is_pii_safe() -> None:
    # The payload schema forbids extra fields and carries no free-text customer content.
    fields = set(OutboxJobData.model_fields)
    assert "customer_message" not in fields
    assert "draft_response_body" not in fields
    assert OUTBOX_PAYLOAD_VERSION in _payload().canonical_json()


def test_backoff_is_bounded_and_deterministic() -> None:
    job_id = uuid.uuid4()
    kwargs = {
        "base_seconds": 2.0,
        "max_seconds": 60.0,
        "jitter_ratio": 0.2,
        "job_id": job_id,
    }
    first = compute_backoff_seconds(attempt=1, **kwargs)
    assert first == compute_backoff_seconds(attempt=1, **kwargs)  # deterministic
    # Monotonic growth up to the cap (compare medians without jitter).
    no_jitter = {**kwargs, "jitter_ratio": 0.0}
    assert compute_backoff_seconds(attempt=1, **no_jitter) == 2.0
    assert compute_backoff_seconds(attempt=2, **no_jitter) == 4.0
    assert compute_backoff_seconds(attempt=10, **no_jitter) == 60.0  # capped
    # Jitter stays within +/- ratio of the capped delay.
    for attempt in range(1, 8):
        capped = compute_backoff_seconds(attempt=attempt, **no_jitter)
        jittered = compute_backoff_seconds(attempt=attempt, **kwargs)
        assert abs(jittered - capped) <= capped * 0.2 + 1e-9
