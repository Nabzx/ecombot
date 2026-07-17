"""Checkpoint serialisation, redaction, hashing and verification.

A checkpoint is an immutable, redacted snapshot of workflow state at a step boundary.
The snapshot is redacted (customer PII/secrets removed) before it is hashed and stored,
and the hash is verified on load so tampering is detected. Snapshots carry a schema
version; an unsupported version is rejected on resume.
"""

from __future__ import annotations

from app.llm.redaction import redact_json
from app.workflows.definition import STATE_SCHEMA_VERSION
from app.workflows.state import SupportWorkflowState, snapshot_hash


class CheckpointError(Exception):
    """Raised when a checkpoint fails hash verification or schema validation."""


def build_snapshot(state: SupportWorkflowState) -> tuple[dict[str, object], str]:
    """Return the redacted snapshot and its deterministic hash."""
    redacted = redact_json(state.snapshot())
    return redacted, snapshot_hash(redacted)


def verify_checkpoint(
    snapshot_json: dict[str, object], stored_hash: str, state_schema_version: str
) -> None:
    """Verify a checkpoint's schema version and hash, or raise CheckpointError."""
    if state_schema_version != STATE_SCHEMA_VERSION:
        raise CheckpointError(
            f"unsupported checkpoint schema version: {state_schema_version!r}"
        )
    if snapshot_hash(snapshot_json) != stored_hash:
        raise CheckpointError("checkpoint hash mismatch (snapshot tampered)")


def restore_state(snapshot_json: dict[str, object]) -> SupportWorkflowState:
    """Rebuild a typed workflow state from a verified snapshot."""
    return SupportWorkflowState.model_validate(snapshot_json)
