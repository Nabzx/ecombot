"""Customer read-only tools. Returns masked PII summaries; never exposes passwords."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.repositories.customer import CustomerRepository
from app.rules.enums import RiskLevel
from app.schemas.customer import CustomerSummary
from app.tools.context import ToolContext
from app.tools.enums import Permission
from app.tools.errors import invalid_input, not_found
from app.tools.registry import RetryPolicy, ToolDefinition
from app.tools.schemas import CustomerSearchResult

_MAX_LIMIT = 25


class SearchCustomerInput(BaseModel):
    email: str | None = None
    external_reference: str | None = None
    name_query: str | None = None
    limit: int = Field(default=10, ge=1, le=_MAX_LIMIT)


class GetCustomerInput(BaseModel):
    customer_id: uuid.UUID


async def search_customer(
    ctx: ToolContext, params: SearchCustomerInput
) -> CustomerSearchResult:
    repo = CustomerRepository(ctx.require_session())
    matches: list[CustomerSummary] = []

    if params.email:
        found = await repo.get_by_email(params.email)
        matches = [CustomerSummary.model_validate(found)] if found else []
    elif params.external_reference:
        found = await repo.get_by_external_reference(params.external_reference)
        matches = [CustomerSummary.model_validate(found)] if found else []
    elif params.name_query:
        page = await repo.search(params.name_query, limit=params.limit)
        matches = [CustomerSummary.model_validate(c) for c in page.items]
    else:
        raise invalid_input("Provide an email, external reference, or name query.")

    return CustomerSearchResult(match_count=len(matches), matches=matches)


async def get_customer(ctx: ToolContext, params: GetCustomerInput) -> CustomerSummary:
    repo = CustomerRepository(ctx.require_session())
    customer = await repo.get(params.customer_id)
    if customer is None:
        raise not_found("Customer not found.")
    return CustomerSummary.model_validate(customer)


TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="search_customer",
        description="Search customers by email, external reference, or name (masked).",
        input_model=SearchCustomerInput,
        output_model=CustomerSearchResult,
        permission=Permission.customer_read,
        risk_level=RiskLevel.read_only,
        read_only=True,
        approval_required=False,
        version="search_customer-v1",
        model_accessible=True,
        retry_policy=RetryPolicy(max_retries=1),
        handler=search_customer,
    ),
    ToolDefinition(
        name="get_customer",
        description="Fetch a single customer by id as a masked summary.",
        input_model=GetCustomerInput,
        output_model=CustomerSummary,
        permission=Permission.customer_read,
        risk_level=RiskLevel.read_only,
        read_only=True,
        approval_required=False,
        version="get_customer-v1",
        model_accessible=True,
        retry_policy=RetryPolicy(max_retries=1),
        handler=get_customer,
    ),
)
