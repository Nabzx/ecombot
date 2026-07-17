"""Typed workflow result and replay-diff models (redacted, safe to return)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.workflows.enums import WorkflowState, WorkflowStatus


class WorkflowRunResult(BaseModel):
    """A concise, redacted summary of a workflow run's outcome."""

    model_config = ConfigDict(extra="forbid")

    run_id: uuid.UUID
    ticket_id: uuid.UUID
    ticket_reference: str
    workflow_name: str
    workflow_version: str
    status: WorkflowStatus
    state: WorkflowState
    step_count: int = 0
    checkpoint_count: int = 0
    classification: str | None = None
    resolved_customer_id: str | None = None
    resolved_order_id: str | None = None
    risk_level: str | None = None
    recommended_route: str | None = None
    proposed_action: str | None = None
    approval_required: bool = False
    required_role: str | None = None
    draft_subject: str | None = None
    citation_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    failure_code: str | None = None
    failure_message: str | None = None
    retry_count: int = 0
    resume_count: int = 0
    replay_source_run_id: uuid.UUID | None = None


class WorkflowDiff(BaseModel):
    """A field-by-field comparison of an original run and its replay."""

    model_config = ConfigDict(extra="forbid")

    source_run_id: uuid.UUID
    replay_run_id: uuid.UUID
    fields: dict[str, dict[str, object]] = Field(default_factory=dict)

    @property
    def identical(self) -> bool:
        return all(entry["source"] == entry["replay"] for entry in self.fields.values())


class WorkflowReplayResult(BaseModel):
    """The result of a replay: the new run plus a diff against the source."""

    model_config = ConfigDict(extra="forbid")

    replay: WorkflowRunResult
    diff: WorkflowDiff
