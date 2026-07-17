"""The typed, serialisable workflow-state carried across steps and checkpointed.

Holds only what resume and replay genuinely need: IDs, typed snapshots and derived
facts — never ORM objects, secrets or hidden reasoning. Raw untrusted customer text is
kept separate from trusted derived facts and is redacted before a checkpoint is stored.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.workflows.definition import STATE_SCHEMA_VERSION
from app.workflows.enums import WorkflowState


class SupportWorkflowState(BaseModel):
    """The full working state of a support-ticket workflow run."""

    model_config = ConfigDict(extra="forbid")

    # identity / control
    state_schema_version: str = STATE_SCHEMA_VERSION
    workflow_run_id: uuid.UUID
    workflow_name: str
    workflow_version: str
    ticket_id: uuid.UUID
    ticket_reference: str
    correlation_id: str
    current_state: WorkflowState = WorkflowState.RECEIVED
    current_step: str = "receive"
    step_index: int = 0

    # raw untrusted input (redacted before persistence)
    raw_ticket_subject: str = ""
    raw_customer_message: str = ""
    injection_flag: bool = False

    # derived / trusted facts
    classification: dict[str, object] | None = None
    identifier_candidates: dict[str, object] | None = None
    # Stored as strings (JSON-native) since they are filled progressively via state
    # fragments; converted to UUID at point of use.
    resolved_customer_id: str | None = None
    resolved_order_id: str | None = None
    customer_match_count: int = 0
    order_match_count: int = 0
    ownership_result: dict[str, object] | None = None
    order_summary: dict[str, object] | None = None
    shipment_summary: dict[str, object] | None = None
    retrieval_request: dict[str, object] | None = None
    retrieval_result: dict[str, object] | None = None
    policy_citations: list[str] = Field(default_factory=list)
    rule_inputs: dict[str, object] | None = None
    rule_results: dict[str, object] | None = None
    evidence_summary: dict[str, object] | None = None
    draft_response: dict[str, object] | None = None
    decision_summary: dict[str, object] | None = None
    proposed_action: str | None = None
    confidence: float | None = None
    risk_level: str | None = None
    recommended_route: str | None = None
    approval_required: bool = False
    required_role: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    # provenance references
    model_call_ids: list[str] = Field(default_factory=list)
    tool_call_ids: list[str] = Field(default_factory=list)
    policy_chunk_ids: list[str] = Field(default_factory=list)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    rule_versions: dict[str, str] = Field(default_factory=dict)
    retrieval_index_version: str | None = None

    def snapshot(self) -> dict[str, object]:
        """A JSON-serialisable snapshot of the full state."""
        return self.model_dump(mode="json")


def snapshot_hash(redacted_snapshot: dict[str, object]) -> str:
    """Deterministic SHA-256 over a canonical, already-redacted snapshot."""
    canonical = json.dumps(redacted_snapshot, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
