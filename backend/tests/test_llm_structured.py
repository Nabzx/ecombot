"""Structured-output extraction, validation and semantic-safety tests."""

from __future__ import annotations

import pytest
from app.llm.enums import ModelErrorCode, ModelTaskType, ProposedAction
from app.llm.schemas import (
    ProposedToolCall,
    ResponseDraftingOutput,
    TicketClassificationOutput,
    ToolPlanningOutput,
)
from app.llm.structured import (
    StructuredOutputError,
    extract_json_object,
    parse_and_validate,
)
from app.llm.tasks.semantic import SemanticContext, validate_semantics
from app.models.enums import TicketCategory

VALID = '{"category":"refund_request","confidence":0.9,"decision_summary":"ok"}'


def test_extract_plain_json() -> None:
    assert extract_json_object(VALID).startswith("{")


def test_extract_from_markdown_fence() -> None:
    wrapped = f"```json\n{VALID}\n```"
    assert extract_json_object(wrapped) == VALID


def test_extract_from_surrounding_prose() -> None:
    noisy = f"Sure, here it is: {VALID} hope that helps"
    assert extract_json_object(noisy) == VALID


def test_extract_no_object_raises() -> None:
    with pytest.raises(StructuredOutputError):
        extract_json_object("no json here")


def test_valid_output_parses() -> None:
    out = parse_and_validate(VALID, TicketClassificationOutput)
    assert isinstance(out, TicketClassificationOutput)
    assert out.category == TicketCategory.refund_request


def test_invalid_json_raises_typed() -> None:
    with pytest.raises(StructuredOutputError) as exc:
        parse_and_validate("{not json", TicketClassificationOutput)
    assert exc.value.code == ModelErrorCode.INVALID_STRUCTURED_OUTPUT


def test_missing_required_field_raises_typed() -> None:
    with pytest.raises(StructuredOutputError) as exc:
        parse_and_validate('{"confidence":0.9}', TicketClassificationOutput)
    assert exc.value.code == ModelErrorCode.SCHEMA_VALIDATION_FAILED


def test_confidence_out_of_range_rejected() -> None:
    bad = '{"category":"refund_request","confidence":1.4,"decision_summary":"x"}'
    with pytest.raises(StructuredOutputError):
        parse_and_validate(bad, TicketClassificationOutput)


def test_invalid_enum_rejected() -> None:
    bad = '{"category":"nonsense","confidence":0.9,"decision_summary":"x"}'
    with pytest.raises(StructuredOutputError):
        parse_and_validate(bad, TicketClassificationOutput)


def test_semantic_drops_forbidden_tools() -> None:
    plan = ToolPlanningOutput(
        tool_calls=[
            ProposedToolCall(
                tool="execute_simulated_refund", arguments={}, purpose="x"
            ),
            ProposedToolCall(
                tool="search_customer",
                arguments={"email": "a@b.com"},
                purpose="ok",
            ),
        ]
    )
    ctx = SemanticContext(allowed_tools=frozenset({"search_customer"}))
    safe = validate_semantics(ModelTaskType.READ_ONLY_TOOL_PLANNING, plan, ctx)
    assert isinstance(safe, ToolPlanningOutput)
    tools = {c.tool for c in safe.tool_calls}
    assert tools == {"search_customer"}


def test_semantic_dedupes_tool_calls() -> None:
    call = ProposedToolCall(
        tool="search_customer", arguments={"email": "a@b.com"}, purpose="x"
    )
    plan = ToolPlanningOutput(tool_calls=[call, call.model_copy()])
    ctx = SemanticContext(allowed_tools=frozenset({"search_customer"}))
    safe = validate_semantics(ModelTaskType.READ_ONLY_TOOL_PLANNING, plan, ctx)
    assert isinstance(safe, ToolPlanningOutput)
    assert len(safe.tool_calls) == 1


def test_semantic_rejects_false_execution_claim() -> None:
    draft = ResponseDraftingOutput(
        subject="s",
        body="Good news, your refund has been issued today.",
        proposed_action=ProposedAction.NO_ACTION,
        decision_summary="d",
    )
    ctx = SemanticContext(allowed_actions=frozenset({ProposedAction.NO_ACTION}))
    with pytest.raises(StructuredOutputError):
        validate_semantics(ModelTaskType.RESPONSE_DRAFTING, draft, ctx)


def test_semantic_rejects_out_of_list_action() -> None:
    draft = ResponseDraftingOutput(
        subject="s",
        body="ok",
        proposed_action=ProposedAction.REQUEST_SUPERVISOR_REFUND_APPROVAL,
        decision_summary="d",
    )
    ctx = SemanticContext(allowed_actions=frozenset({ProposedAction.NO_ACTION}))
    with pytest.raises(StructuredOutputError):
        validate_semantics(ModelTaskType.RESPONSE_DRAFTING, draft, ctx)


def test_semantic_drops_unsupplied_citations() -> None:
    draft = ResponseDraftingOutput(
        subject="s",
        body="ok",
        proposed_action=ProposedAction.NO_ACTION,
        citations=["POL-FAKE:v1:x:chunk-00", "POL-REAL:v1:x:chunk-00"],
        decision_summary="d",
    )
    ctx = SemanticContext(
        allowed_actions=frozenset({ProposedAction.NO_ACTION}),
        supplied_citations=frozenset({"POL-REAL:v1:x:chunk-00"}),
    )
    safe = validate_semantics(ModelTaskType.RESPONSE_DRAFTING, draft, ctx)
    assert isinstance(safe, ResponseDraftingOutput)
    assert safe.citations == ["POL-REAL:v1:x:chunk-00"]
