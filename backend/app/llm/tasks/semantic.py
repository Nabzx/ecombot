"""Task-specific semantic validation applied after schema validation.

Schema validation guarantees shape; these checks enforce call-time safety invariants
that cannot be expressed as a static schema: category/action allowlists, tool allowlists
and argument schemas, citations restricted to supplied evidence, and the absence of
false-execution claims. Unsafe content is either *sanitised out* (so it can never reach
a consumer) or rejected with a typed error that triggers the single repair attempt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from app.llm.enums import ModelErrorCode, ModelTaskType, ProposedAction
from app.llm.schemas import (
    DecisionSummaryOutput,
    EvidenceSummaryOutput,
    IdentifierExtractionOutput,
    ResponseDraftingOutput,
    TicketClassificationOutput,
    ToolPlanningOutput,
)
from app.llm.structured import StructuredOutputError
from app.models.enums import TicketCategory
from app.tools.registry import get_tool

# Phrases that would falsely claim a write/execution already happened. The model can
# never execute anything, so any of these in a draft is a hard safety violation.
FALSE_EXECUTION_PHRASES: tuple[str, ...] = (
    "has been issued",
    "has been refunded",
    "have refunded",
    "has been processed",
    "refund is complete",
    "has been cancelled",
    "have cancelled",
    "has been canceled",
    "order is cancelled",
    "money has been returned",
    "we have refunded",
)


@dataclass(frozen=True)
class SemanticContext:
    """Call-time facts the semantic validators check output against."""

    allowed_categories: frozenset[TicketCategory] = field(default_factory=frozenset)
    customer_text: str = ""
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    max_tool_calls: int = 8
    supplied_citations: frozenset[str] = field(default_factory=frozenset)
    allowed_actions: frozenset[ProposedAction] = field(default_factory=frozenset)
    warnings: list[str] = field(default_factory=list)


def validate_semantics(
    task_type: ModelTaskType, output: BaseModel, context: SemanticContext
) -> BaseModel:
    """Dispatch to the per-task validator, returning a sanitised, safe output."""
    if isinstance(output, TicketClassificationOutput):
        return _classification(output, context)
    if isinstance(output, IdentifierExtractionOutput):
        return _identifiers(output, context)
    if isinstance(output, ToolPlanningOutput):
        return _tool_planning(output, context)
    if isinstance(output, EvidenceSummaryOutput):
        return _evidence_summary(output, context)
    if isinstance(output, ResponseDraftingOutput):
        return _response_drafting(output, context)
    if isinstance(output, DecisionSummaryOutput):
        return _decision_summary(output, context)
    return output


def _classification(
    output: TicketClassificationOutput, context: SemanticContext
) -> TicketClassificationOutput:
    if context.allowed_categories and output.category not in context.allowed_categories:
        raise StructuredOutputError(
            ModelErrorCode.SCHEMA_VALIDATION_FAILED,
            f"category {output.category} is not in the allowed list",
        )
    # Alternatives must be strictly lower confidence than the primary and in-allowlist.
    alternatives = [
        alt
        for alt in output.alternative_categories
        if alt.confidence < output.confidence
        and (
            not context.allowed_categories or alt.category in context.allowed_categories
        )
    ]
    return output.model_copy(update={"alternative_categories": alternatives})


def _identifiers(
    output: IdentifierExtractionOutput, context: SemanticContext
) -> IdentifierExtractionOutput:
    text = context.customer_text
    if not text:
        return output

    def keep(identifier: object) -> object:
        if identifier is None:
            return None
        value = getattr(identifier, "value", "")
        if value and value in text:
            return identifier
        context.warnings.append(f"dropped hallucinated identifier: {value!r}")
        return None

    skus = [s for s in output.product_skus if s in text]
    return output.model_copy(
        update={
            "customer_email": keep(output.customer_email),
            "customer_reference": keep(output.customer_reference),
            "order_number": keep(output.order_number),
            "tracking_number": keep(output.tracking_number),
            "product_skus": skus,
        }
    )


def _tool_planning(
    output: ToolPlanningOutput, context: SemanticContext
) -> ToolPlanningOutput:
    kept = []
    seen: set[tuple[str, str]] = set()
    for call in output.tool_calls:
        if call.tool not in context.allowed_tools:
            context.warnings.append(f"dropped disallowed tool: {call.tool!r}")
            continue
        definition = get_tool(call.tool)
        if definition is None or not (
            definition.read_only and definition.model_accessible
        ):
            context.warnings.append(f"dropped non-read-only tool: {call.tool!r}")
            continue
        try:
            definition.input_model.model_validate(call.arguments)
        except ValidationError:
            context.warnings.append(f"dropped tool with invalid args: {call.tool!r}")
            continue
        signature = (call.tool, repr(sorted(call.arguments.items())))
        if signature in seen:
            continue
        seen.add(signature)
        kept.append(call)
    kept = kept[: context.max_tool_calls]
    return output.model_copy(
        update={
            "tool_calls": kept,
            "requires_more_information": output.requires_more_information or not kept,
        }
    )


def _evidence_summary(
    output: EvidenceSummaryOutput, context: SemanticContext
) -> EvidenceSummaryOutput:
    citations = [c for c in output.citations if c in context.supplied_citations]
    dropped = set(output.citations) - set(citations)
    if dropped:
        context.warnings.append(f"dropped unsupplied citations: {sorted(dropped)}")
    return output.model_copy(update={"citations": citations})


def _response_drafting(
    output: ResponseDraftingOutput, context: SemanticContext
) -> ResponseDraftingOutput:
    if (
        context.allowed_actions
        and output.proposed_action not in context.allowed_actions
    ):
        raise StructuredOutputError(
            ModelErrorCode.SCHEMA_VALIDATION_FAILED,
            f"proposed_action {output.proposed_action} is not in the allowed list",
        )
    lowered = output.body.lower()
    for phrase in FALSE_EXECUTION_PHRASES:
        if phrase in lowered:
            raise StructuredOutputError(
                ModelErrorCode.SCHEMA_VALIDATION_FAILED,
                f"draft falsely claims execution: {phrase!r}",
            )
    citations = [c for c in output.citations if c in context.supplied_citations]
    dropped = set(output.citations) - set(citations)
    if dropped:
        context.warnings.append(f"dropped unsupplied citations: {sorted(dropped)}")
    return output.model_copy(update={"citations": citations})


def _decision_summary(
    output: DecisionSummaryOutput, context: SemanticContext
) -> DecisionSummaryOutput:
    evidence = [
        cited
        for cited in output.policy_evidence
        if cited.citation_id in context.supplied_citations
    ]
    return output.model_copy(update={"policy_evidence": evidence})
