"""Builders that assemble a ``ModelTaskRequest`` from primitive inputs.

Each builder keeps the rendered prompt context, the semantic-validation context and the
deterministic mock payload in lock-step, so a task is callable identically from the CLI,
the dev API and the evaluation runner. Builders are pure (no DB access); callers load
the primitives (ticket text, evidence, rule results) and pass them in.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping

from app.llm.enums import ModelTaskType, ProposedAction
from app.llm.service import ModelTaskRequest
from app.llm.tasks.definitions import MODEL_ACCESSIBLE_TOOLS
from app.llm.tasks.semantic import SemanticContext
from app.models.enums import TicketCategory

ALL_CATEGORIES = tuple(c.value for c in TicketCategory)


def _corr() -> str:
    return uuid.uuid4().hex


def format_evidence(citations: list[str], excerpts: Mapping[str, str]) -> str:
    """Render supplied evidence as labelled citation → excerpt lines."""
    if not citations:
        return "(no evidence supplied)"
    return "\n".join(
        f"[{cid}] {excerpts.get(cid, '(excerpt omitted)')}" for cid in citations
    )


def build_classification_request(
    *,
    subject: str,
    message: str,
    injection_flag: bool = False,
    allowed_categories: Iterable[str] = ALL_CATEGORIES,
    order_context: str = "none",
    ticket_id: uuid.UUID | None = None,
    mock_scenario: str = "",
) -> ModelTaskRequest:
    categories = list(allowed_categories)
    return ModelTaskRequest(
        task_type=ModelTaskType.TICKET_CLASSIFICATION,
        render_context={
            "allowed_categories": ", ".join(categories),
            "injection_flag": str(injection_flag).lower(),
            "customer_subject": subject,
            "customer_message": message,
            "order_context": order_context,
        },
        semantic_context=SemanticContext(
            allowed_categories=frozenset(TicketCategory(c) for c in categories),
            customer_text=message,
        ),
        mock_payload={"customer_text": message, "allowed_categories": categories},
        mock_scenario=mock_scenario,
        correlation_id=_corr(),
        ticket_id=ticket_id,
    )


def build_identifier_request(
    *,
    message: str,
    ticket_id: uuid.UUID | None = None,
    mock_scenario: str = "",
) -> ModelTaskRequest:
    return ModelTaskRequest(
        task_type=ModelTaskType.IDENTIFIER_EXTRACTION,
        render_context={"customer_message": message},
        semantic_context=SemanticContext(customer_text=message),
        mock_payload={"customer_text": message},
        mock_scenario=mock_scenario,
        correlation_id=_corr(),
        ticket_id=ticket_id,
    )


def build_tool_planning_request(
    *,
    category: str,
    message: str,
    known_email: str | None = None,
    max_tool_calls: int = 4,
    ticket_id: uuid.UUID | None = None,
    mock_scenario: str = "",
) -> ModelTaskRequest:
    known_context = f"known_email={known_email or 'unknown'}"
    return ModelTaskRequest(
        task_type=ModelTaskType.READ_ONLY_TOOL_PLANNING,
        render_context={
            "category": category,
            "allowed_tools": ", ".join(MODEL_ACCESSIBLE_TOOLS),
            "customer_message": message,
            "known_context": known_context,
            "max_tool_calls": str(max_tool_calls),
        },
        semantic_context=SemanticContext(
            allowed_tools=frozenset(MODEL_ACCESSIBLE_TOOLS),
            max_tool_calls=max_tool_calls,
            customer_text=message,
        ),
        mock_payload={
            "category": category,
            "allowed_tools": list(MODEL_ACCESSIBLE_TOOLS),
            "known_email": known_email,
            "customer_text": message,
            "max_tool_calls": max_tool_calls,
        },
        mock_scenario=mock_scenario,
        correlation_id=_corr(),
        ticket_id=ticket_id,
    )


def build_evidence_summary_request(
    *,
    topic: str,
    citations: list[str],
    excerpts: Mapping[str, str],
    support_status: str,
    conflict_status: str,
    rule_result: str = "(none)",
    mock_scenario: str = "",
) -> ModelTaskRequest:
    return ModelTaskRequest(
        task_type=ModelTaskType.EVIDENCE_SUMMARY,
        render_context={
            "topic": topic,
            "support_status": support_status,
            "conflict_status": conflict_status,
            "evidence_block": format_evidence(citations, excerpts),
            "rule_result": rule_result,
        },
        semantic_context=SemanticContext(supplied_citations=frozenset(citations)),
        mock_payload={
            "citations": citations,
            "support_status": support_status,
            "conflict_status": conflict_status,
        },
        mock_scenario=mock_scenario,
        correlation_id=_corr(),
    )


def build_response_drafting_request(
    *,
    customer_name: str,
    category: str,
    message: str,
    rule_result: str,
    allowed_actions: Iterable[str],
    approval_required: bool,
    requires_more_information: bool,
    citations: list[str],
    excerpts: Mapping[str, str] | None = None,
    order_summary: str = "(none)",
    shipment_summary: str = "(none)",
    evidence_summary: str = "(none)",
    missing_information: list[str] | None = None,
    mock_scenario: str = "",
) -> ModelTaskRequest:
    actions = list(allowed_actions)
    excerpts = excerpts or {}
    return ModelTaskRequest(
        task_type=ModelTaskType.RESPONSE_DRAFTING,
        render_context={
            "customer_name": customer_name,
            "category": category,
            "customer_message": message,
            "order_summary": order_summary,
            "shipment_summary": shipment_summary,
            "rule_result": rule_result,
            "evidence_block": format_evidence(citations, excerpts),
            "evidence_summary": evidence_summary,
            "allowed_actions": ", ".join(actions),
            "approval_required": str(approval_required).lower(),
            "requires_more_information": str(requires_more_information).lower(),
        },
        semantic_context=SemanticContext(
            supplied_citations=frozenset(citations),
            allowed_actions=frozenset(ProposedAction(a) for a in actions),
            customer_text=message,
        ),
        mock_payload={
            "customer_name": customer_name,
            "allowed_actions": actions,
            "approval_required": approval_required,
            "requires_more_info": requires_more_information,
            "citations": citations,
            "missing_information": missing_information or [],
            "rule_outcome": rule_result,
        },
        mock_scenario=mock_scenario,
        correlation_id=_corr(),
    )


def build_decision_summary_request(
    *,
    customer_intent: str,
    verified_facts: list[str],
    citations: list[str],
    excerpts: Mapping[str, str],
    rule_result: str,
    next_step: str,
    approval_required: bool,
    mock_scenario: str = "",
) -> ModelTaskRequest:
    return ModelTaskRequest(
        task_type=ModelTaskType.DECISION_SUMMARY,
        render_context={
            "customer_intent_hint": customer_intent,
            "verified_facts": "; ".join(verified_facts) or "(none)",
            "evidence_block": format_evidence(citations, excerpts),
            "rule_result": rule_result,
            "proposed_next_step": next_step,
            "approval_required": str(approval_required).lower(),
        },
        semantic_context=SemanticContext(supplied_citations=frozenset(citations)),
        mock_payload={
            "customer_intent": customer_intent,
            "verified_facts": verified_facts,
            "policy_evidence": [
                {"citation_id": cid, "summary": excerpts.get(cid, "")[:200]}
                for cid in citations
            ],
            "rule_outcome": rule_result,
            "next_step": next_step,
            "approval_required": approval_required,
        },
        mock_scenario=mock_scenario,
        correlation_id=_corr(),
    )
