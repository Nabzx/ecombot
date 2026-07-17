"""Strict Pydantic v2 schemas for every model-task input and output.

Outputs forbid extra fields, use strict enums, bound confidences to [0, 1] and cap free
text, so a provider cannot smuggle unexpected structure past validation. Semantic checks
that need call-time context (allowlists, supplied citations) live in
``app.llm.tasks.semantic`` — these schemas enforce only shape and intrinsic bounds.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.llm.enums import ProposedAction
from app.models.enums import TicketCategory

_MAX_SUMMARY = 600
_MAX_TEXT = 4000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentifierSource(StrEnum):
    EXPLICIT = "explicit"
    POSSIBLE = "possible"
    AMBIGUOUS = "ambiguous"


# --- ticket classification ---------------------------------------------------------
class CategoryConfidence(_Strict):
    category: TicketCategory
    confidence: float = Field(ge=0.0, le=1.0)


class TicketClassificationOutput(_Strict):
    category: TicketCategory
    confidence: float = Field(ge=0.0, le=1.0)
    alternative_categories: list[CategoryConfidence] = Field(
        default_factory=list, max_length=3
    )
    requires_clarification: bool = False
    missing_information: list[str] = Field(default_factory=list, max_length=10)
    decision_summary: str = Field(max_length=_MAX_SUMMARY)


# --- identifier extraction ---------------------------------------------------------
class ExtractedIdentifier(_Strict):
    value: str = Field(max_length=200)
    source: IdentifierSource
    confidence: float = Field(ge=0.0, le=1.0)


class IdentifierExtractionOutput(_Strict):
    customer_email: ExtractedIdentifier | None = None
    customer_reference: ExtractedIdentifier | None = None
    order_number: ExtractedIdentifier | None = None
    tracking_number: ExtractedIdentifier | None = None
    product_skus: list[str] = Field(default_factory=list, max_length=20)
    ambiguities: list[str] = Field(default_factory=list, max_length=10)


# --- read-only tool planning -------------------------------------------------------
class ProposedToolCall(_Strict):
    tool: str = Field(max_length=100)
    arguments: dict[str, object] = Field(default_factory=dict)
    purpose: str = Field(max_length=300)


class ToolPlanningOutput(_Strict):
    tool_calls: list[ProposedToolCall] = Field(default_factory=list, max_length=8)
    requires_more_information: bool = False
    missing_information: list[str] = Field(default_factory=list, max_length=10)


# --- evidence summary --------------------------------------------------------------
class EvidenceSummaryOutput(_Strict):
    summary: str = Field(max_length=_MAX_TEXT)
    citations: list[str] = Field(default_factory=list, max_length=20)
    unsupported_points: list[str] = Field(default_factory=list, max_length=10)
    conflict_warning: bool = False
    sufficient_for_drafting: bool = False


# --- response drafting -------------------------------------------------------------
class ResponseDraftingOutput(_Strict):
    subject: str = Field(max_length=200)
    body: str = Field(max_length=_MAX_TEXT)
    citations: list[str] = Field(default_factory=list, max_length=20)
    proposed_action: ProposedAction
    approval_required: bool = False
    requires_human_review: bool = False
    unsupported_claims: list[str] = Field(default_factory=list, max_length=10)
    decision_summary: str = Field(max_length=_MAX_SUMMARY)
    missing_information: list[str] = Field(default_factory=list, max_length=10)


# --- decision summary --------------------------------------------------------------
class CitedEvidence(_Strict):
    citation_id: str = Field(max_length=200)
    summary: str = Field(max_length=_MAX_SUMMARY)


class DecisionSummaryOutput(_Strict):
    customer_intent: str = Field(max_length=_MAX_SUMMARY)
    verified_facts: list[str] = Field(default_factory=list, max_length=20)
    policy_evidence: list[CitedEvidence] = Field(default_factory=list, max_length=20)
    rule_outcome: str = Field(max_length=_MAX_SUMMARY)
    next_step: str = Field(max_length=_MAX_SUMMARY)
    approval_required: bool = False
    uncertainties: list[str] = Field(default_factory=list, max_length=10)


# Registry of output schema name -> model, used by the structured-output pipeline.
OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "TicketClassificationOutput": TicketClassificationOutput,
    "IdentifierExtractionOutput": IdentifierExtractionOutput,
    "ToolPlanningOutput": ToolPlanningOutput,
    "EvidenceSummaryOutput": EvidenceSummaryOutput,
    "ResponseDraftingOutput": ResponseDraftingOutput,
    "DecisionSummaryOutput": DecisionSummaryOutput,
}
