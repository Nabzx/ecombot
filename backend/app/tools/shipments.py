"""Shipment read-only tool. Customer-scoped; fictional carrier data only."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.repositories.order import OrderRepository
from app.repositories.shipment import ShipmentRepository
from app.rules.enums import RiskLevel
from app.schemas.shipment import ShipmentDetail
from app.tools.context import ToolContext
from app.tools.enums import Permission
from app.tools.errors import not_found, ownership_mismatch
from app.tools.registry import RetryPolicy, ToolDefinition


class GetShipmentInput(BaseModel):
    order_id: uuid.UUID
    customer_id: uuid.UUID


async def get_shipment_status(
    ctx: ToolContext, params: GetShipmentInput
) -> ShipmentDetail:
    if ctx.customer_scope is not None and ctx.customer_scope != params.customer_id:
        raise ownership_mismatch("Request is outside the resolved customer scope.")
    session = ctx.require_session()
    order = await OrderRepository(session).get(params.order_id)
    if order is None:
        raise not_found("Order not found.")
    if order.customer_id != params.customer_id:
        raise ownership_mismatch("The order does not belong to this customer.")
    shipment = await ShipmentRepository(session).get_by_order(params.order_id)
    if shipment is None:
        raise not_found("No shipment exists for this order.")
    return ShipmentDetail.model_validate(shipment)


TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="get_shipment_status",
        description="Fetch delivery status and dates for a customer-scoped order.",
        input_model=GetShipmentInput,
        output_model=ShipmentDetail,
        permission=Permission.shipment_read,
        risk_level=RiskLevel.read_only,
        read_only=True,
        approval_required=False,
        version="get_shipment_status-v1",
        model_accessible=True,
        retry_policy=RetryPolicy(max_retries=1),
        handler=get_shipment_status,
    ),
)
