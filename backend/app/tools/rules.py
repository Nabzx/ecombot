"""Deterministic rule tools.

Thin async wrappers over the pure rule functions so they can be invoked through the same
registry/executor/permission machinery. They are system-facing (``model_accessible`` is
False): the future workflow calls them; their results are authoritative over the LLM.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.rules.cancellations import CancellationInput, check_cancellation_eligibility
from app.rules.deliveries import (
    DeliveryDelayInput,
    MissingDeliveryInput,
    check_missing_delivery,
    classify_delivery_delay,
)
from app.rules.enums import ActionType, RiskLevel
from app.rules.idempotency import generate_idempotency_key
from app.rules.models import RuleResult
from app.rules.ownership import OwnershipInput, check_ownership
from app.rules.refunds import (
    RefundInput,
    calculate_refund_limit,
    check_refund_eligibility,
)
from app.rules.remedies import (
    DamagedItemInput,
    IncorrectItemInput,
    check_damaged_item_remedy,
    check_incorrect_item_remedy,
)
from app.rules.returns import ReturnInput, check_return_eligibility
from app.rules.routing import RoutingInput, calculate_risk_and_route
from app.tools.context import ToolContext
from app.tools.enums import Permission
from app.tools.registry import ToolDefinition, ToolHandler
from app.tools.schemas import IdempotencyKeyResult, RefundLimitResult


class IdempotencyInput(BaseModel):
    ticket_id: uuid.UUID
    action_type: ActionType
    order_id: uuid.UUID
    amount_pence: int | None = None


async def _check_ownership(ctx: ToolContext, params: OwnershipInput) -> RuleResult:
    return check_ownership(params)


async def _check_return(ctx: ToolContext, params: ReturnInput) -> RuleResult:
    return check_return_eligibility(params, ctx.clock)


async def _check_refund(ctx: ToolContext, params: RefundInput) -> RuleResult:
    return check_refund_eligibility(params)


async def _calculate_refund_limit(
    ctx: ToolContext, params: RefundInput
) -> RefundLimitResult:
    remaining = max(0, params.order_total_paid_pence - params.prior_refunded_pence)
    return RefundLimitResult(
        maximum_refund_pence=calculate_refund_limit(params),
        item_line_total_pence=params.item_line_total_pence,
        remaining_order_balance_pence=remaining,
    )


async def _check_cancellation(
    ctx: ToolContext, params: CancellationInput
) -> RuleResult:
    return check_cancellation_eligibility(params)


async def _classify_delay(ctx: ToolContext, params: DeliveryDelayInput) -> RuleResult:
    return classify_delivery_delay(params, ctx.clock)


async def _check_missing(ctx: ToolContext, params: MissingDeliveryInput) -> RuleResult:
    return check_missing_delivery(params, ctx.clock)


async def _check_damaged(ctx: ToolContext, params: DamagedItemInput) -> RuleResult:
    return check_damaged_item_remedy(params, ctx.clock)


async def _check_incorrect(ctx: ToolContext, params: IncorrectItemInput) -> RuleResult:
    return check_incorrect_item_remedy(params, ctx.clock)


async def _risk_and_route(ctx: ToolContext, params: RoutingInput) -> RuleResult:
    return calculate_risk_and_route(params)


async def _idempotency(
    ctx: ToolContext, params: IdempotencyInput
) -> IdempotencyKeyResult:
    key = generate_idempotency_key(
        ticket_id=params.ticket_id,
        action_type=params.action_type,
        order_id=params.order_id,
        amount_pence=params.amount_pence,
    )
    return IdempotencyKeyResult(idempotency_key=key)


def _rule_tool(
    name: str,
    description: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    handler: ToolHandler,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_model=input_model,
        output_model=output_model,
        permission=Permission.rules_execute,
        risk_level=RiskLevel.read_only,
        read_only=True,
        approval_required=False,
        version=f"{name}-v1",
        model_accessible=False,
        handler=handler,
    )


TOOLS: tuple[ToolDefinition, ...] = (
    _rule_tool(
        "check_ownership",
        "Deterministic ownership check.",
        OwnershipInput,
        RuleResult,
        _check_ownership,
    ),
    _rule_tool(
        "check_return_eligibility",
        "Deterministic return eligibility.",
        ReturnInput,
        RuleResult,
        _check_return,
    ),
    _rule_tool(
        "check_refund_eligibility",
        "Deterministic refund eligibility.",
        RefundInput,
        RuleResult,
        _check_refund,
    ),
    _rule_tool(
        "calculate_refund_limit",
        "Maximum refundable amount.",
        RefundInput,
        RefundLimitResult,
        _calculate_refund_limit,
    ),
    _rule_tool(
        "check_cancellation_eligibility",
        "Deterministic cancellation eligibility.",
        CancellationInput,
        RuleResult,
        _check_cancellation,
    ),
    _rule_tool(
        "classify_delivery_delay",
        "Classify delivery delay tier.",
        DeliveryDelayInput,
        RuleResult,
        _classify_delay,
    ),
    _rule_tool(
        "check_missing_delivery",
        "Handle a missing-delivery claim.",
        MissingDeliveryInput,
        RuleResult,
        _check_missing,
    ),
    _rule_tool(
        "check_damaged_item_remedy",
        "Damaged-item remedy eligibility.",
        DamagedItemInput,
        RuleResult,
        _check_damaged,
    ),
    _rule_tool(
        "check_incorrect_item_remedy",
        "Incorrect-item remedy eligibility.",
        IncorrectItemInput,
        RuleResult,
        _check_incorrect,
    ),
    _rule_tool(
        "calculate_risk_and_route",
        "Risk classification and routing.",
        RoutingInput,
        RuleResult,
        _risk_and_route,
    ),
    _rule_tool(
        "generate_idempotency_key",
        "Deterministic idempotency key.",
        IdempotencyInput,
        IdempotencyKeyResult,
        _idempotency,
    ),
)
