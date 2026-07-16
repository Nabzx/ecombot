"""Order read-only tools. Always customer-scoped; ownership mismatch is blocked."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.repositories.order import OrderRepository
from app.rules.enums import RiskLevel
from app.schemas.order import OrderDetail, OrderSummary
from app.tools.context import ToolContext
from app.tools.enums import Permission
from app.tools.errors import not_found, ownership_mismatch
from app.tools.registry import RetryPolicy, ToolDefinition
from app.tools.schemas import OrderSearchResult

_MAX_LIMIT = 50


class SearchOrderInput(BaseModel):
    customer_id: uuid.UUID
    order_number: str | None = None
    limit: int = Field(default=20, ge=1, le=_MAX_LIMIT)


class GetOrderInput(BaseModel):
    order_id: uuid.UUID
    customer_id: uuid.UUID


def _enforce_scope(ctx: ToolContext, customer_id: uuid.UUID) -> None:
    """A ticket workflow sets ctx.customer_scope; searches must stay within it."""
    if ctx.customer_scope is not None and ctx.customer_scope != customer_id:
        raise ownership_mismatch("Request is outside the resolved customer scope.")


async def search_order(ctx: ToolContext, params: SearchOrderInput) -> OrderSearchResult:
    _enforce_scope(ctx, params.customer_id)
    repo = OrderRepository(ctx.require_session())
    if params.order_number:
        orders = await repo.search_for_customer(params.customer_id, params.order_number)
        summaries = [OrderSummary.model_validate(o) for o in orders[: params.limit]]
        return OrderSearchResult(match_count=len(summaries), orders=summaries)
    page = await repo.list_for_customer(params.customer_id, limit=params.limit)
    summaries = [OrderSummary.model_validate(o) for o in page.items]
    return OrderSearchResult(match_count=page.total, orders=summaries)


async def get_order(ctx: ToolContext, params: GetOrderInput) -> OrderDetail:
    _enforce_scope(ctx, params.customer_id)
    repo = OrderRepository(ctx.require_session())
    order = await repo.get_with_items(params.order_id)
    # Do not leak another customer's order existence: same safe error either way.
    if order is None or order.customer_id != params.customer_id:
        if order is not None and order.customer_id != params.customer_id:
            raise ownership_mismatch("The order does not belong to this customer.")
        raise not_found("Order not found.")
    return OrderDetail.model_validate(order)


TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="search_order",
        description="Search a customer's own orders by order number (customer-scoped).",
        input_model=SearchOrderInput,
        output_model=OrderSearchResult,
        permission=Permission.order_read,
        risk_level=RiskLevel.read_only,
        read_only=True,
        approval_required=False,
        version="search_order-v1",
        model_accessible=True,
        retry_policy=RetryPolicy(max_retries=1),
        handler=search_order,
    ),
    ToolDefinition(
        name="get_order",
        description="Fetch a customer-scoped order with items and shipment.",
        input_model=GetOrderInput,
        output_model=OrderDetail,
        permission=Permission.order_read,
        risk_level=RiskLevel.read_only,
        read_only=True,
        approval_required=False,
        version="get_order-v1",
        model_accessible=True,
        retry_policy=RetryPolicy(max_retries=1),
        handler=get_order,
    ),
)
