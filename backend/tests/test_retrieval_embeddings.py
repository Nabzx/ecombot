"""Embedding-provider tests."""

from __future__ import annotations

import pytest
from app.core.config import Settings
from app.retrieval.constants import EMBEDDING_DIM
from app.retrieval.embeddings import (
    DeterministicHashEmbedding,
    get_embedding_provider,
)


async def test_deterministic_and_dimension() -> None:
    provider = DeterministicHashEmbedding()
    assert provider.dimension == EMBEDDING_DIM
    a = await provider.embed_query("return an opened item after 30 days")
    b = await provider.embed_query("return an opened item after 30 days")
    assert a == b
    assert len(a) == EMBEDDING_DIM


async def test_different_inputs_differ() -> None:
    provider = DeterministicHashEmbedding()
    a = await provider.embed_query("refund policy")
    b = await provider.embed_query("cancellation policy")
    assert a != b


async def test_empty_input_rejected() -> None:
    provider = DeterministicHashEmbedding()
    with pytest.raises(ValueError, match="empty"):
        await provider.embed_query("   ")


async def test_batch_order_preserved() -> None:
    provider = DeterministicHashEmbedding()
    texts = ["returns", "refunds", "cancellations"]
    vectors = await provider.embed_documents(texts)
    assert len(vectors) == 3
    singles = [await provider.embed_query(t) for t in texts]
    assert vectors == singles


async def test_optional_provider_unavailable_is_clear() -> None:
    provider = get_embedding_provider(
        Settings(embedding_provider="sentence_transformers", jwt_secret="t")
    )
    with pytest.raises(RuntimeError, match="sentence-transformers"):
        await provider.embed_query("hello")
