"""The inspectable model-task registry.

Each :class:`ModelTaskDefinition` declares everything about a task without running it:
prompt, schemas, tool allowlist, limits, retry/fallback policy and which trust-labelled
context it carries. The tool allowlist is derived from the S3 tool registry so it can
only ever contain model-accessible, read-only tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.llm.enums import ModelTaskType
from app.tools.registry import list_tools


def _model_accessible_read_only_tools() -> tuple[str, ...]:
    """The only tools a model may ever propose: model-accessible and read-only."""
    return tuple(
        sorted(t.name for t in list_tools() if t.model_accessible and t.read_only)
    )


# Frozen allowlist computed once from the registry. Rule tools (model_accessible=False),
# reserved write tools and approval/execution tools are structurally excluded.
MODEL_ACCESSIBLE_TOOLS: tuple[str, ...] = _model_accessible_read_only_tools()


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 2  # attempts per provider, across backoff
    fallback_allowed: bool = True


@dataclass(frozen=True, slots=True)
class ModelTaskDefinition:
    """Everything needed to run and reason about a task, inspectable offline."""

    task_type: ModelTaskType
    purpose: str
    prompt_name: str
    input_schema_name: str
    output_schema_name: str | None
    allowed_tools: tuple[str, ...] = ()
    max_input_chars: int = 12_000
    max_output_tokens: int = 640
    timeout_seconds: float = 30.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    mock_supported: bool = True
    includes_customer_text: bool = False
    includes_evidence: bool = False
    includes_rule_results: bool = False
    redaction_required: bool = True


TASK_DEFINITIONS: dict[ModelTaskType, ModelTaskDefinition] = {
    ModelTaskType.TICKET_CLASSIFICATION: ModelTaskDefinition(
        task_type=ModelTaskType.TICKET_CLASSIFICATION,
        purpose="Classify a ticket into one of the ten frozen categories.",
        prompt_name="ticket-classification",
        input_schema_name="TicketClassificationInput",
        output_schema_name="TicketClassificationOutput",
        max_input_chars=8_000,
        max_output_tokens=512,
        includes_customer_text=True,
    ),
    ModelTaskType.IDENTIFIER_EXTRACTION: ModelTaskDefinition(
        task_type=ModelTaskType.IDENTIFIER_EXTRACTION,
        purpose="Extract candidate identifiers from customer text (untrusted).",
        prompt_name="identifier-extraction",
        input_schema_name="IdentifierExtractionInput",
        output_schema_name="IdentifierExtractionOutput",
        max_input_chars=8_000,
        max_output_tokens=512,
        includes_customer_text=True,
    ),
    ModelTaskType.READ_ONLY_TOOL_PLANNING: ModelTaskDefinition(
        task_type=ModelTaskType.READ_ONLY_TOOL_PLANNING,
        purpose="Propose read-only tool calls to gather facts (proposals only).",
        prompt_name="read-only-tool-planning",
        input_schema_name="ToolPlanningInput",
        output_schema_name="ToolPlanningOutput",
        allowed_tools=MODEL_ACCESSIBLE_TOOLS,
        max_input_chars=8_000,
        max_output_tokens=640,
        includes_customer_text=True,
    ),
    ModelTaskType.EVIDENCE_SUMMARY: ModelTaskDefinition(
        task_type=ModelTaskType.EVIDENCE_SUMMARY,
        purpose="Summarise official policy evidence already retrieved by S3.",
        prompt_name="evidence-summary",
        input_schema_name="EvidenceSummaryInput",
        output_schema_name="EvidenceSummaryOutput",
        max_input_chars=12_000,
        max_output_tokens=640,
        includes_evidence=True,
        includes_rule_results=True,
    ),
    ModelTaskType.RESPONSE_DRAFTING: ModelTaskDefinition(
        task_type=ModelTaskType.RESPONSE_DRAFTING,
        purpose="Draft a grounded customer response reflecting deterministic rules.",
        prompt_name="response-drafting",
        input_schema_name="ResponseDraftingInput",
        output_schema_name="ResponseDraftingOutput",
        max_input_chars=16_000,
        max_output_tokens=1_024,
        includes_customer_text=True,
        includes_evidence=True,
        includes_rule_results=True,
    ),
    ModelTaskType.DECISION_SUMMARY: ModelTaskDefinition(
        task_type=ModelTaskType.DECISION_SUMMARY,
        purpose="Produce a concise internal decision summary (no chain-of-thought).",
        prompt_name="decision-summary",
        input_schema_name="DecisionSummaryInput",
        output_schema_name="DecisionSummaryOutput",
        max_input_chars=12_000,
        max_output_tokens=640,
        includes_evidence=True,
        includes_rule_results=True,
    ),
    ModelTaskType.STRUCTURED_OUTPUT_REPAIR: ModelTaskDefinition(
        task_type=ModelTaskType.STRUCTURED_OUTPUT_REPAIR,
        purpose="Repair one invalid structured output to conform to its schema.",
        prompt_name="structured-output-repair",
        input_schema_name="RepairInput",
        output_schema_name=None,  # output schema is the target task's schema
        max_input_chars=16_000,
        max_output_tokens=1_024,
        retry_policy=RetryPolicy(max_attempts=1, fallback_allowed=False),
    ),
}


def get_task_definition(task_type: ModelTaskType) -> ModelTaskDefinition:
    return TASK_DEFINITIONS[task_type]
