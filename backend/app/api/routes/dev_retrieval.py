"""Development-only, read-only retrieval inspection endpoints.

Mounted only when ``ENVIRONMENT`` is development or test (see ``create_app``). No
writes, no reindexing, no arbitrary source selection, no full-document dumping. Uses the
retrieval service rather than duplicating logic.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.models.policy import PolicyChunk
from app.retrieval.constants import (
    DEFAULT_TOP_K,
    MAX_EXCERPT_CHARS,
    MAX_QUERY_CHARS,
    MAX_TOP_K,
)
from app.retrieval.embeddings import get_embedding_provider
from app.retrieval.models import PolicyRetrievalRequest, RetrievalMode
from app.retrieval.repository import RetrievalRepository
from app.retrieval.service import PolicyRetrievalService
from app.rules.clock import SystemClock
from app.tools.retrieval import SearchPoliciesResult, search_policies_result_from

router = APIRouter(prefix="/api/dev/retrieval", tags=["dev-retrieval"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class DevSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    topic: str | None = None
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    mode: RetrievalMode = RetrievalMode.hybrid


class DevStats(BaseModel):
    total_chunks: int
    indexed_versions: int


class DevCitation(BaseModel):
    citation_id: str
    topic: str
    policy_title: str
    version: int
    section_path: str
    heading: str | None
    excerpt: str


@router.get("/stats", response_model=DevStats)
async def stats(session: SessionDep) -> DevStats:
    total = (await session.execute(select(func.count(PolicyChunk.id)))).scalar_one()
    versions = (
        await session.execute(
            select(func.count(func.distinct(PolicyChunk.policy_version_id)))
        )
    ).scalar_one()
    return DevStats(total_chunks=total, indexed_versions=versions)


@router.post("/search", response_model=SearchPoliciesResult)
async def search(
    body: DevSearchRequest, session: SessionDep, settings: SettingsDep
) -> SearchPoliciesResult:
    service = PolicyRetrievalService(session, get_embedding_provider(settings))
    result = await service.retrieve(
        PolicyRetrievalRequest(
            query=body.query, topic=body.topic, top_k=body.top_k, mode=body.mode
        ),
        clock=SystemClock(),
    )
    return search_policies_result_from(result)


@router.get("/citations/{citation_id}", response_model=DevCitation)
async def citation(citation_id: str, session: SessionDep) -> DevCitation:
    candidate = await RetrievalRepository(session).get_by_citation_id(citation_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Citation not found")
    chunk = candidate.chunk
    return DevCitation(
        citation_id=chunk.citation_id,
        topic=candidate.policy.topic,
        policy_title=candidate.policy.title,
        version=candidate.version.version,
        section_path=chunk.section_path,
        heading=chunk.heading,
        excerpt=chunk.body[:MAX_EXCERPT_CHARS],
    )
