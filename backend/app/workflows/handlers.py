"""The twelve workflow step handlers.

Each handler is a pure orchestration step: it runs read-only tools, model tasks,
retrieval or deterministic rules, then returns a typed :class:`StepExecutionResult` with
a state fragment and a destination the runner validates against the transition table.
Handlers never execute a consequential action; the deterministic ``inspect_ticket``
authority decides ownership, eligibility, risk and route.
"""

from __future__ import annotations

import time
import uuid
from typing import Protocol

from pydantic import BaseModel

from app.llm.service import ModelTaskRequest, ModelTaskResult
from app.llm.tasks import builders
from app.models.customer import Customer
from app.models.enums import TicketCategory
from app.models.ticket import Ticket
from app.repositories.order import OrderRepository
from app.repositories.ticket import TicketRepository
from app.rules.enums import (
    ActionType,
    ApprovalRole,
    DecisionOutcome,
    ReasonCode,
    RiskLevel,
    Route,
)
from app.rules.ownership import OwnershipInput, check_ownership
from app.rules.routing import RoutingInput, calculate_risk_and_route
from app.rules.service import inspect_ticket
from app.tools.context import ToolContext
from app.tools.enums import READ_PERMISSIONS
from app.tools.executor import execute_tool
from app.tools.models import ToolResult
from app.tools.registry import get_tool
from app.workflows.context import StepExecutionResult, WorkflowExecutionContext
from app.workflows.enums import ProposedActionStatus, WorkflowState
from app.workflows.repository import WorkflowRepository
from app.workflows.state import SupportWorkflowState

MAX_MESSAGE_CHARS = 8000


def _as_dict(value: object) -> dict[str, object]:
    """Narrow an ``object``-typed state field to a dict for safe ``.get`` access."""
    return value if isinstance(value, dict) else {}


# Categories that fundamentally require an order to proceed.
_ORDER_REQUIRED = frozenset(
    {
        TicketCategory.refund_request,
        TicketCategory.return_request,
        TicketCategory.cancellation_request,
        TicketCategory.damaged_item,
        TicketCategory.incorrect_item,
        TicketCategory.missing_delivery,
        TicketCategory.order_tracking,
        TicketCategory.delayed_delivery,
    }
)

# Deterministic route → workflow destination state.
_ROUTE_TO_STATE: dict[Route, WorkflowState] = {
    Route.await_agent: WorkflowState.AWAITING_AGENT,
    Route.await_supervisor: WorkflowState.AWAITING_APPROVAL,
    Route.needs_information: WorkflowState.NEEDS_INFORMATION,
    Route.escalate: WorkflowState.ESCALATED,
    Route.manual_handling: WorkflowState.ESCALATED,
    Route.blocked: WorkflowState.BLOCKED,
    Route.continue_processing: WorkflowState.AWAITING_AGENT,
}

# Route → the proposed actions the drafting task may choose from.
_ROUTE_ACTIONS: dict[Route, list[str]] = {
    Route.await_agent: ["provide_tracking_information", "provide_policy_information"],
    Route.await_supervisor: [
        "request_supervisor_refund_approval",
        "request_supervisor_cancellation_approval",
        "escalate_to_supervisor",
    ],
    Route.needs_information: ["request_more_information"],
    Route.escalate: ["escalate_to_supervisor", "escalate_to_support_agent"],
    Route.manual_handling: ["escalate_to_support_agent"],
    Route.blocked: ["escalate_to_supervisor"],
    Route.continue_processing: ["provide_policy_information"],
}


class StepHandler(Protocol):
    name: str
    source_state: WorkflowState

    async def __call__(
        self, ctx: WorkflowExecutionContext, state: SupportWorkflowState
    ) -> StepExecutionResult: ...


# --- shared helpers ----------------------------------------------------------------
async def _load_ticket(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> Ticket | None:
    return await TicketRepository(ctx.session).get_with_messages(state.ticket_id)


def _tool_context(
    ctx: WorkflowExecutionContext, customer_scope: uuid.UUID | None
) -> ToolContext:
    return ToolContext(
        permissions=READ_PERMISSIONS,
        clock=ctx.clock,
        session=ctx.session,
        actor="workflow",
        customer_scope=customer_scope,
        correlation_id=ctx.correlation_id,
    )


async def _run_tool(
    ctx: WorkflowExecutionContext,
    step_id: uuid.UUID | None,
    name: str,
    args: dict[str, object],
    customer_scope: uuid.UUID | None = None,
) -> tuple[ToolResult[BaseModel], str | None]:
    """Execute a read-only tool and persist a redacted WorkflowToolCall record."""
    from app.llm.redaction import redact_json

    definition = get_tool(name)
    version = definition.version if definition else "unknown"
    start = time.perf_counter()
    result = await execute_tool(name, _tool_context(ctx, customer_scope), args)
    duration = int((time.perf_counter() - start) * 1000)

    call_id: str | None = None
    if ctx.workflow_run_id is not None and step_id is not None:
        repo = WorkflowRepository(ctx.session)
        output = (
            redact_json(result.data.model_dump(mode="json"))
            if result.ok and result.data is not None
            else None
        )
        row = await repo.record_tool_call(
            run_id=ctx.workflow_run_id,
            step_id=step_id,
            tool_name=name,
            tool_version=version,
            status="ok" if result.ok else "error",
            input_json=redact_json(dict(args)),
            output_json=output,
            error_code=result.error.code.value if result.error else None,
            retryable=False,
            duration_ms=duration,
            correlation_id=ctx.correlation_id,
            now=ctx.clock.now(),
        )
        call_id = str(row.id)
    return result, call_id


async def _run_model(
    ctx: WorkflowExecutionContext, request: ModelTaskRequest
) -> ModelTaskResult:
    request.workflow_run_id = ctx.workflow_run_id
    request.workflow_step_id = ctx.current_step_id
    request.correlation_id = ctx.correlation_id
    if ctx.mock_scenario:
        request.mock_scenario = ctx.mock_scenario
    return await ctx.model_service.run_task(request, session=ctx.session)


# --- 1. receive --------------------------------------------------------------------
async def receive(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> StepExecutionResult:
    return StepExecutionResult(destination_state=WorkflowState.VALIDATING)


# --- 2. validate -------------------------------------------------------------------
async def validate(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> StepExecutionResult:
    ticket = await _load_ticket(ctx, state)
    if ticket is None:
        return StepExecutionResult(
            destination_state=WorkflowState.FAILED_VALIDATION,
            failure_code="validation_failed",
            error_message="ticket not found",
        )
    message = ""
    for msg in ticket.messages:
        if msg.sender.value == "customer":
            message = msg.body
            break
    subject = ticket.subject or ""
    if not subject.strip() and not message.strip():
        return StepExecutionResult(
            destination_state=WorkflowState.FAILED_VALIDATION,
            failure_code="validation_failed",
            error_message="ticket has no subject or customer message",
        )
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[:MAX_MESSAGE_CHARS]
    return StepExecutionResult(
        destination_state=WorkflowState.SANITISING,
        state_fragment={
            "raw_ticket_subject": subject,
            "raw_customer_message": message,
            "injection_flag": ticket.injection_flag,
        },
    )


# --- 3. sanitise -------------------------------------------------------------------
async def sanitise(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> StepExecutionResult:
    warnings = []
    if state.injection_flag:
        warnings.append("prompt_injection_detected")
    # Never block classification on injection; routing forces escalation later.
    return StepExecutionResult(
        destination_state=WorkflowState.CLASSIFYING,
        state_fragment={"injection_flag": state.injection_flag},
        warnings=warnings,
    )


# --- 4. classify -------------------------------------------------------------------
async def classify(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> StepExecutionResult:
    request = builders.build_classification_request(
        subject=state.raw_ticket_subject,
        message=state.raw_customer_message,
        injection_flag=state.injection_flag,
        ticket_id=state.ticket_id,
    )
    result = await _run_model(ctx, request)
    if not result.success or result.output is None:
        return StepExecutionResult(
            destination_state=WorkflowState.ESCALATED,
            warnings=["classification_failed"],
            failure_code="model_failed",
        )
    output = result.output.model_dump(mode="json")
    return StepExecutionResult(
        destination_state=WorkflowState.EXTRACTING_IDENTIFIERS,
        state_fragment={
            "classification": output,
            "confidence": output.get("confidence"),
        },
        model_call_ids=[str(result.model_call_id)] if result.model_call_id else [],
    )


# --- 5. extract identifiers --------------------------------------------------------
async def extract_identifiers(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> StepExecutionResult:
    request = builders.build_identifier_request(
        message=state.raw_customer_message, ticket_id=state.ticket_id
    )
    result = await _run_model(ctx, request)
    if not result.success or result.output is None:
        return StepExecutionResult(
            destination_state=WorkflowState.ESCALATED,
            warnings=["extraction_failed"],
            failure_code="model_failed",
        )
    return StepExecutionResult(
        destination_state=WorkflowState.RESOLVING_CUSTOMER,
        state_fragment={"identifier_candidates": result.output.model_dump(mode="json")},
        model_call_ids=[str(result.model_call_id)] if result.model_call_id else [],
    )


# --- 6. resolve customer -----------------------------------------------------------
async def resolve_customer(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> StepExecutionResult:
    ticket = await _load_ticket(ctx, state)
    if ticket is None or ticket.customer_id is None:
        return StepExecutionResult(
            destination_state=WorkflowState.NEEDS_INFORMATION,
            state_fragment={"customer_match_count": 0},
            warnings=["customer_unresolved"],
        )
    return StepExecutionResult(
        destination_state=WorkflowState.RESOLVING_ORDER,
        state_fragment={
            "resolved_customer_id": str(ticket.customer_id),
            "customer_match_count": 1,
        },
    )


# --- 7. resolve order --------------------------------------------------------------
async def resolve_order(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> StepExecutionResult:
    ticket = await _load_ticket(ctx, state)
    assert ticket is not None and ticket.customer_id is not None  # noqa: S101
    orders = OrderRepository(ctx.session)

    order = None
    if ticket.order_id is not None:
        order = await orders.get_with_items(ticket.order_id)
    else:
        candidates = state.identifier_candidates or {}
        raw = candidates.get("order_number")
        number = raw.get("value") if isinstance(raw, dict) else None
        if number:
            order = await orders.get_by_number(str(number))

    ownership = check_ownership(
        OwnershipInput(
            resolved_customer_id=ticket.customer_id,
            customer_match_count=1,
            resolved_order_id=order.id if order else None,
            order_customer_id=order.customer_id if order else None,
            order_match_count=1 if order else 0,
        )
    )
    ownership_json = ownership.model_dump(mode="json")
    if ownership.outcome.value == "blocked":
        # Cross-customer ownership mismatch — never reveals the other customer's order.
        return StepExecutionResult(
            destination_state=WorkflowState.BLOCKED,
            state_fragment={"ownership_result": ownership_json, "order_match_count": 0},
            failure_code="ownership_blocked",
            warnings=["cross_customer_block"],
        )
    if order is not None:
        return StepExecutionResult(
            destination_state=WorkflowState.RETRIEVING_ORDER_DATA,
            state_fragment={
                "resolved_order_id": str(order.id),
                "order_match_count": 1,
                "ownership_result": ownership_json,
            },
        )
    if ticket.category == TicketCategory.product_policy_question:
        return StepExecutionResult(
            destination_state=WorkflowState.RETRIEVING_ORDER_DATA,
            state_fragment={"order_match_count": 0, "ownership_result": ownership_json},
        )
    return StepExecutionResult(
        destination_state=WorkflowState.NEEDS_INFORMATION,
        state_fragment={"order_match_count": 0, "ownership_result": ownership_json},
        warnings=["order_unresolved"],
    )


# --- 8. retrieve order data --------------------------------------------------------
async def retrieve_order_data(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> StepExecutionResult:
    if state.resolved_order_id is None:
        return StepExecutionResult(destination_state=WorkflowState.RETRIEVING_POLICY)
    order_id = uuid.UUID(state.resolved_order_id)
    customer_id = (
        uuid.UUID(state.resolved_customer_id) if state.resolved_customer_id else None
    )
    tool_ids: list[str] = []
    order_result, cid = await _run_tool(
        ctx,
        ctx.current_step_id,
        "get_order",
        {"order_id": str(order_id), "customer_id": str(customer_id)},
        customer_scope=customer_id,
    )
    if cid:
        tool_ids.append(cid)
    if not order_result.ok:
        return StepExecutionResult(
            destination_state=WorkflowState.FAILED_DEPENDENCY,
            tool_call_ids=tool_ids,
            retryable=True,
            failure_code="dependency_unavailable",
            error_message="order lookup failed",
        )
    shipment_result, scid = await _run_tool(
        ctx,
        ctx.current_step_id,
        "get_shipment_status",
        {"order_id": str(order_id), "customer_id": str(customer_id)},
        customer_scope=customer_id,
    )
    if scid:
        tool_ids.append(scid)
    order_summary = (
        order_result.data.model_dump(mode="json")
        if order_result.data is not None
        else {}
    )
    shipment_summary = (
        shipment_result.data.model_dump(mode="json")
        if shipment_result.ok and shipment_result.data is not None
        else {}
    )
    return StepExecutionResult(
        destination_state=WorkflowState.RETRIEVING_POLICY,
        state_fragment={
            "order_summary": order_summary,
            "shipment_summary": shipment_summary,
        },
        tool_call_ids=tool_ids,
    )


# --- 9. retrieve policy ------------------------------------------------------------
_CATEGORY_TOPIC: dict[TicketCategory, str] = {
    TicketCategory.return_request: "returns",
    TicketCategory.refund_request: "refunds",
    TicketCategory.cancellation_request: "cancellations",
    TicketCategory.damaged_item: "damaged_items",
    TicketCategory.incorrect_item: "incorrect_items",
    TicketCategory.missing_delivery: "missing_deliveries",
    TicketCategory.delayed_delivery: "delivery_delays",
    TicketCategory.order_tracking: "delivery_delays",
    TicketCategory.product_policy_question: "returns",
}


async def retrieve_policy(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> StepExecutionResult:
    ticket = await _load_ticket(ctx, state)
    assert ticket is not None  # noqa: S101
    topic = _CATEGORY_TOPIC.get(ticket.category, "returns")
    query = f"{ticket.subject} {state.raw_customer_message}".strip()[:200]
    result, cid = await _run_tool(
        ctx,
        ctx.current_step_id,
        "search_policies",
        {"query": query, "topic": topic},
    )
    tool_ids = [cid] if cid else []
    if not result.ok or result.data is None:
        return StepExecutionResult(
            destination_state=WorkflowState.FAILED_DEPENDENCY,
            tool_call_ids=tool_ids,
            retryable=True,
            failure_code="dependency_unavailable",
            error_message="policy retrieval failed",
        )
    data = result.data.model_dump(mode="json")
    raw_citations = data.get("citations", [])
    citations: list[str] = []
    if isinstance(raw_citations, list):
        for entry in raw_citations:
            if isinstance(entry, dict) and "citation_id" in entry:
                citations.append(str(entry["citation_id"]))
            else:
                citations.append(str(entry))
    support = str(data.get("support_status", "unsupported"))
    conflict = str(data.get("conflict_status", "none"))
    fragment: dict[str, object] = {
        "retrieval_result": data,
        "policy_citations": citations,
        "retrieval_index_version": data.get("index_version"),
    }
    # Never silently resolve a policy conflict.
    if conflict == "conflicting":
        return StepExecutionResult(
            destination_state=WorkflowState.ESCALATED,
            state_fragment=fragment,
            citation_ids=citations,
            tool_call_ids=tool_ids,
            warnings=["policy_conflict"],
        )
    if (
        support == "unsupported"
        and ticket.category == TicketCategory.product_policy_question
    ):
        return StepExecutionResult(
            destination_state=WorkflowState.ESCALATED,
            state_fragment=fragment,
            tool_call_ids=tool_ids,
            warnings=["policy_unsupported"],
        )
    return StepExecutionResult(
        destination_state=WorkflowState.EVALUATING_RULES,
        state_fragment=fragment,
        citation_ids=citations,
        tool_call_ids=tool_ids,
    )


# --- 10. evaluate rules ------------------------------------------------------------
# Deterministic outcomes that imply a genuine consequential action (else informational).
_CONSEQUENTIAL_OUTCOMES = frozenset(
    {
        DecisionOutcome.eligible,
        DecisionOutcome.requires_approval,
        DecisionOutcome.requires_review,
    }
)
# Category → the consequential action a supervisor would approve (drives routing risk).
_CATEGORY_ACTION: dict[TicketCategory, ActionType] = {
    TicketCategory.refund_request: ActionType.refund,
    TicketCategory.return_request: ActionType.return_rma,
    TicketCategory.cancellation_request: ActionType.cancellation,
    TicketCategory.damaged_item: ActionType.replacement,
    TicketCategory.incorrect_item: ActionType.replacement,
}


async def evaluate_rules(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> StepExecutionResult:
    # Deterministic ownership/eligibility authority for this ticket.
    inspection = await inspect_ticket(ctx.session, state.ticket_reference, ctx.clock)
    category = inspection.category
    category_result = inspection.category_result

    # Route using the workflow's *live* model classification confidence (the ticket's
    # stored confidence is None until this workflow classifies it). A consequential
    # action only exists when the deterministic category result actually permits one;
    # an ineligible/blocked outcome is informational and needs no approval.
    consequential = (
        category_result is not None
        and category_result.outcome in _CONSEQUENTIAL_OUTCOMES
    )
    if consequential and category_result is not None:
        action = _CATEGORY_ACTION.get(category, ActionType.information)
        action_risk = category_result.risk_level
    else:
        action = ActionType.information
        action_risk = RiskLevel.read_only
    delivered_but_disputed = category_result is not None and category_result.has(
        ReasonCode.DELIVERED_BUT_DISPUTED
    )
    routing = calculate_risk_and_route(
        RoutingInput(
            classification_confidence=state.confidence,
            ticket_category=category,
            ownership_blocked=inspection.ownership.outcome.value == "blocked",
            injection_flag=state.injection_flag,
            proposed_action=action,
            action_risk=action_risk,
            delivered_but_disputed=delivered_but_disputed,
        )
    )
    snapshot = {
        "ownership": inspection.ownership.model_dump(mode="json"),
        "category_result": (
            category_result.model_dump(mode="json") if category_result else None
        ),
        "routing": routing.model_dump(mode="json"),
        "idempotency_key": inspection.idempotency_key,
        "category": category.value,
    }
    fragment: dict[str, object] = {
        "rule_results": snapshot,
        "rule_versions": {"routing": routing.rule_version},
        "risk_level": routing.risk_level.value,
    }
    if inspection.ownership.outcome.value == "blocked":
        return StepExecutionResult(
            destination_state=WorkflowState.BLOCKED,
            state_fragment=fragment,
            failure_code="ownership_blocked",
            warnings=["ownership_blocked"],
        )
    if routing.route == Route.needs_information:
        return StepExecutionResult(
            destination_state=WorkflowState.NEEDS_INFORMATION,
            state_fragment=fragment,
            warnings=["missing_information"],
        )
    return StepExecutionResult(
        destination_state=WorkflowState.SUMMARISING_EVIDENCE,
        state_fragment=fragment,
    )


# --- 11. summarise evidence --------------------------------------------------------
def _excerpts(state: SupportWorkflowState) -> dict[str, str]:
    return {citation: "" for citation in state.policy_citations}


async def summarise_evidence(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> StepExecutionResult:
    rule_results = state.rule_results or {}
    routing = _as_dict(rule_results.get("routing", {}))
    support = "supported" if state.policy_citations else "unsupported"
    request = builders.build_evidence_summary_request(
        topic=str(rule_results.get("category", "returns")),
        citations=list(state.policy_citations),
        excerpts=_excerpts(state),
        support_status=support,
        conflict_status="none",
        rule_result=str(routing.get("outcome", "")),
    )
    result = await _run_model(ctx, request)
    if not result.success or result.output is None:
        return StepExecutionResult(
            destination_state=WorkflowState.ESCALATED,
            warnings=["evidence_summary_failed"],
            failure_code="model_failed",
        )
    return StepExecutionResult(
        destination_state=WorkflowState.DRAFTING_RESPONSE,
        state_fragment={"evidence_summary": result.output.model_dump(mode="json")},
        model_call_ids=[str(result.model_call_id)] if result.model_call_id else [],
    )


# --- 12. draft response ------------------------------------------------------------
def _route_from_state(state: SupportWorkflowState) -> Route:
    routing = _as_dict(_as_dict(state.rule_results).get("routing", {}))
    try:
        return Route(str(routing.get("route", "await_agent")))
    except ValueError:  # pragma: no cover - defensive
        return Route.await_agent


async def draft_response(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> StepExecutionResult:
    ticket = await _load_ticket(ctx, state)
    assert ticket is not None  # noqa: S101
    route = _route_from_state(state)
    allowed_actions = _ROUTE_ACTIONS.get(route, ["escalate_to_support_agent"])
    approval_required = route == Route.await_supervisor
    name = "there"
    if ticket.customer_id is not None:
        customer = await ctx.session.get(Customer, ticket.customer_id)
        if customer is not None:
            name = customer.first_name or "there"
    routing = _as_dict(_as_dict(state.rule_results).get("routing", {}))
    request = builders.build_response_drafting_request(
        customer_name=name,
        category=ticket.category.value,
        message=state.raw_customer_message,
        rule_result=str(routing.get("outcome", "")),
        allowed_actions=allowed_actions,
        approval_required=approval_required,
        requires_more_information=route == Route.needs_information,
        citations=list(state.policy_citations),
        excerpts=_excerpts(state),
        evidence_summary=str(_as_dict(state.evidence_summary).get("summary", "")),
    )
    result = await _run_model(ctx, request)
    if not result.success or result.output is None:
        return StepExecutionResult(
            destination_state=WorkflowState.ESCALATED,
            warnings=["drafting_failed"],
            failure_code="model_failed",
        )
    output = result.output.model_dump(mode="json")
    out_citations = output.get("citations", [])
    return StepExecutionResult(
        destination_state=WorkflowState.CALCULATING_ROUTE,
        state_fragment={
            "draft_response": output,
            "proposed_action": output.get("proposed_action"),
            "approval_required": bool(output.get("approval_required")),
            "decision_summary": {"summary": output.get("decision_summary", "")},
        },
        model_call_ids=[str(result.model_call_id)] if result.model_call_id else [],
        citation_ids=[str(c) for c in out_citations]
        if isinstance(out_citations, list)
        else [],
    )


# --- 13. calculate route -----------------------------------------------------------
_ROUTE_PROPOSAL_STATUS: dict[Route, ProposedActionStatus] = {
    Route.await_agent: ProposedActionStatus.READY_FOR_AGENT,
    Route.await_supervisor: ProposedActionStatus.AWAITING_APPROVAL,
    Route.blocked: ProposedActionStatus.BLOCKED,
    Route.escalate: ProposedActionStatus.DRAFT,
    Route.manual_handling: ProposedActionStatus.DRAFT,
    Route.needs_information: ProposedActionStatus.DRAFT,
    Route.continue_processing: ProposedActionStatus.DRAFT,
}


async def calculate_route(
    ctx: WorkflowExecutionContext, state: SupportWorkflowState
) -> StepExecutionResult:
    route = _route_from_state(state)
    destination = _ROUTE_TO_STATE.get(route, WorkflowState.ESCALATED)
    routing = _as_dict(_as_dict(state.rule_results).get("routing", {}))
    approval_required = route == Route.await_supervisor
    required_role = None
    role = routing.get("approval_role")
    if role and role != ApprovalRole.none.value:
        required_role = str(role)
    draft = _as_dict(state.draft_response)
    idem = _as_dict(state.rule_results).get("idempotency_key")

    # Persist / supersede the proposed action (never approved or executed).
    if ctx.workflow_run_id is not None:
        repo = WorkflowRepository(ctx.session)
        await repo.supersede_proposals(ctx.workflow_run_id)
        await repo.create_proposal(
            run_id=ctx.workflow_run_id,
            ticket_id=state.ticket_id,
            action_type=str(state.proposed_action or "no_action"),
            status=_ROUTE_PROPOSAL_STATUS.get(route, ProposedActionStatus.DRAFT),
            risk_level=str(state.risk_level or "read_only"),
            required_role=required_role,
            approval_required=approval_required,
            amount_pence=None,
            idempotency_key=str(idem) if idem else None,
            draft_response_subject=str(draft.get("subject", "")),
            draft_response_body=str(draft.get("body", "")),
            citation_ids=list(state.policy_citations),
            rule_result_json=state.rule_results or {},
            decision_summary_json=state.decision_summary or {},
        )
    return StepExecutionResult(
        destination_state=destination,
        state_fragment={
            "recommended_route": route.value,
            "approval_required": approval_required,
            "required_role": required_role,
        },
    )


HANDLERS: dict[str, StepHandler] = {
    "receive": receive,  # type: ignore[dict-item]
    "validate": validate,  # type: ignore[dict-item]
    "sanitise": sanitise,  # type: ignore[dict-item]
    "classify": classify,  # type: ignore[dict-item]
    "extract_identifiers": extract_identifiers,  # type: ignore[dict-item]
    "resolve_customer": resolve_customer,  # type: ignore[dict-item]
    "resolve_order": resolve_order,  # type: ignore[dict-item]
    "retrieve_order_data": retrieve_order_data,  # type: ignore[dict-item]
    "retrieve_policy": retrieve_policy,  # type: ignore[dict-item]
    "evaluate_rules": evaluate_rules,  # type: ignore[dict-item]
    "summarise_evidence": summarise_evidence,  # type: ignore[dict-item]
    "draft_response": draft_response,  # type: ignore[dict-item]
    "calculate_route": calculate_route,  # type: ignore[dict-item]
}


def get_handler(name: str) -> StepHandler:
    return HANDLERS[name]
