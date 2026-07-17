"""Property-based tests for model-layer invariants."""

from __future__ import annotations

import asyncio
import json

from app.llm.enums import ModelTaskType, ProposedAction
from app.llm.models import ModelParameters, ModelRequest
from app.llm.providers.mock import MockProvider
from app.llm.redaction import redact_text
from app.llm.schemas import ProposedToolCall, ResponseDraftingOutput, ToolPlanningOutput
from app.llm.tasks.definitions import MODEL_ACCESSIBLE_TOOLS
from app.llm.tasks.semantic import SemanticContext, validate_semantics
from app.prompts.registry import get_prompt_registry
from hypothesis import given
from hypothesis import strategies as st

_TOOL_NAMES = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=20)


@given(name=st.sampled_from([d.name for d in get_prompt_registry().all_definitions()]))
def test_prompt_hash_stable(name: str) -> None:
    registry = get_prompt_registry()
    definition = next(d for d in registry.all_definitions() if d.name == name)
    assert definition.template_hash == definition.template_hash
    assert len(definition.template_hash) == 64


@given(tools=st.lists(_TOOL_NAMES, max_size=6))
def test_no_tool_outside_allowlist_survives(tools: list[str]) -> None:
    plan = ToolPlanningOutput(
        tool_calls=[ProposedToolCall(tool=t, arguments={}, purpose="x") for t in tools]
    )
    ctx = SemanticContext(allowed_tools=frozenset(MODEL_ACCESSIBLE_TOOLS))
    safe = validate_semantics(ModelTaskType.READ_ONLY_TOOL_PLANNING, plan, ctx)
    assert isinstance(safe, ToolPlanningOutput)
    for call in safe.tool_calls:
        assert call.tool in MODEL_ACCESSIBLE_TOOLS


@given(
    citations=st.lists(st.text(min_size=1, max_size=12), max_size=6),
    supplied=st.lists(st.text(min_size=1, max_size=12), max_size=6),
)
def test_no_citation_outside_supplied_survives(
    citations: list[str], supplied: list[str]
) -> None:
    draft = ResponseDraftingOutput(
        subject="s",
        body="ok",
        proposed_action=ProposedAction.NO_ACTION,
        citations=citations,
        decision_summary="d",
    )
    ctx = SemanticContext(
        allowed_actions=frozenset({ProposedAction.NO_ACTION}),
        supplied_citations=frozenset(supplied),
    )
    safe = validate_semantics(ModelTaskType.RESPONSE_DRAFTING, draft, ctx)
    assert isinstance(safe, ResponseDraftingOutput)
    for citation in safe.citations:
        assert citation in set(supplied)


@given(text=st.text(max_size=200))
def test_identical_mock_requests_are_identical(text: str) -> None:
    mock = MockProvider()
    request = ModelRequest(
        task_type=ModelTaskType.TICKET_CLASSIFICATION,
        model="mock-deterministic-v1",
        system_message="s",
        user_message="u",
        parameters=ModelParameters(),
        correlation_id="c",
        trace_metadata={"mock_payload": json.dumps({"customer_text": text})},
    )
    first = asyncio.run(mock.generate(request))
    second = asyncio.run(mock.generate(request))
    assert first.raw_text == second.raw_text


_SECRETS = ["jane@example.com", "sk-ABCDEF1234567890XYZ", "07911123456"]


@given(secret=st.sampled_from(_SECRETS), pad=st.text(max_size=40))
def test_redaction_never_leaves_known_secret(secret: str, pad: str) -> None:
    assert secret not in redact_text(f"{pad} {secret} {pad}")
