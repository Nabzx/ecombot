"""The model-facing ``search_policies`` tool.

Restricted by construction: it only ever retrieves authoritative active official policy
(no source-type override, no historical mode, no raw vectors, no full internal metadata,
no whole documents). It returns citations, support/conflict status and warnings.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.models.enums import PolicyStatus
from app.retrieval.constants import DEFAULT_TOP_K, MAX_QUERY_CHARS, MAX_TOP_K
from app.retrieval.embeddings import get_embedding_provider
from app.retrieval.models import (
    ConflictStatus,
    PolicyRetrievalRequest,
    PolicyRetrievalResult,
    RetrievalMode,
    SupportStatus,
)
from app.retrieval.service import PolicyRetrievalService
from app.rules.enums import RiskLevel
from app.tools.context import ToolContext
from app.tools.enums import Permission
from app.tools.registry import RetryPolicy, ToolDefinition


class SearchPoliciesInput(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    topic: str | None = None
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)


class ModelCitation(BaseModel):
    citation_id: str
    topic: str
    policy_title: str
    version: int
    status: PolicyStatus
    effective_from: date
    effective_to: date | None
    section_path: str
    heading: str | None
    excerpt: str
    score: float


class SearchPoliciesResult(BaseModel):
    support_status: SupportStatus
    conflict_status: ConflictStatus
    retrieval_mode: RetrievalMode
    warnings: list[str]
    citations: list[ModelCitation]


def search_policies_result_from(
    result: PolicyRetrievalResult,
) -> SearchPoliciesResult:
    """Map a full retrieval result to the restricted, model-facing shape."""
    citations = [
        ModelCitation(
            citation_id=item.citation_id,
            topic=item.topic,
            policy_title=item.policy_title,
            version=item.version,
            status=item.status,
            effective_from=item.effective_from,
            effective_to=item.effective_to,
            section_path=item.section_path,
            heading=item.heading,
            excerpt=item.excerpt,
            score=round(item.hybrid_score, 6),
        )
        for item in result.evidence
    ]
    return SearchPoliciesResult(
        support_status=result.support_status,
        conflict_status=result.conflict_status,
        retrieval_mode=result.mode_used,
        warnings=result.warnings,
        citations=citations,
    )


async def search_policies(
    ctx: ToolContext, params: SearchPoliciesInput
) -> SearchPoliciesResult:
    session = ctx.require_session()
    provider = get_embedding_provider(get_settings())
    service = PolicyRetrievalService(session, provider)
    # Model-facing: default (official-only) source types; no historical override.
    result = await service.retrieve(
        PolicyRetrievalRequest(
            query=params.query,
            topic=params.topic,
            top_k=params.top_k,
            mode=RetrievalMode.hybrid,
            correlation_id=ctx.correlation_id,
        ),
        clock=ctx.clock,
    )
    return search_policies_result_from(result)


TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="search_policies",
        description="Hybrid semantic + lexical search over active official policy, "
        "returning cited evidence with support and conflict status.",
        input_model=SearchPoliciesInput,
        output_model=SearchPoliciesResult,
        permission=Permission.policy_read,
        risk_level=RiskLevel.read_only,
        read_only=True,
        approval_required=False,
        version="search_policies-v1",
        model_accessible=True,
        retry_policy=RetryPolicy(max_retries=1),
        handler=search_policies,
    ),
)
