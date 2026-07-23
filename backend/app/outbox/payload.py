"""Versioned, typed, PII-safe outbox payload and canonical hashing (S6).

The payload is the immutable instruction the worker executes: exactly which internal
action, for which order and amount, bound to the approval decision and evidence that
authorised it. It carries identifiers and hashes only — never the customer's message,
the full policy text or any model reasoning. The SHA-256 payload hash covers every
field, so any post-creation change is detectable, and an unsupported payload version
fails safe rather than executing.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.actions.enums import ExecutionActionType

OUTBOX_PAYLOAD_VERSION = "outbox-payload-v1"
SUPPORTED_PAYLOAD_VERSIONS = frozenset({OUTBOX_PAYLOAD_VERSION})


class PayloadError(Exception):
    """Raised when a stored outbox payload is unsupported, invalid or tampered."""


class OutboxJobData(BaseModel):
    """The canonical, serialisable instruction for one execution job."""

    model_config = ConfigDict(extra="forbid")

    payload_version: str = OUTBOX_PAYLOAD_VERSION
    outbox_job_id: uuid.UUID
    approval_request_id: uuid.UUID
    approval_decision_id: uuid.UUID
    proposed_action_id: uuid.UUID
    workflow_run_id: uuid.UUID
    workflow_name: str
    workflow_version: str
    ticket_id: uuid.UUID
    customer_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    order_item_id: uuid.UUID | None = None
    action_type: ExecutionActionType
    approved_amount_pence: int | None = None
    currency: str = "GBP"
    business_idempotency_key: str
    approval_snapshot_hash: str
    rule_result_hash: str
    evidence_snapshot_hash: str
    policy_version_ids: list[str] = Field(default_factory=list)
    policy_content_hashes: list[str] = Field(default_factory=list)
    created_at: datetime

    def canonical_json(self) -> str:
        """Deterministic, sorted-key JSON over every field."""
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, ensure_ascii=True
        )

    def compute_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def load_payload(payload_json: dict[str, object], stored_hash: str) -> OutboxJobData:
    """Rebuild and verify a stored payload (raises on unsupported version / tamper)."""
    version = payload_json.get("payload_version")
    if version not in SUPPORTED_PAYLOAD_VERSIONS:
        raise PayloadError(f"unsupported outbox payload version: {version!r}")
    try:
        payload = OutboxJobData.model_validate(payload_json)
    except ValueError as exc:
        raise PayloadError("outbox payload is structurally invalid") from exc
    if payload.compute_hash() != stored_hash:
        raise PayloadError("outbox payload hash mismatch (tampered)")
    return payload
