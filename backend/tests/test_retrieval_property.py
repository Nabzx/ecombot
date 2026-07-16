"""Property-based retrieval tests (Hypothesis)."""

from __future__ import annotations

from app.retrieval.chunker import chunk_markdown
from app.retrieval.query import EmptyQueryError, normalise_query
from hypothesis import given
from hypothesis import strategies as st

_markdownish = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=400
)


@given(body=_markdownish, title=st.text(min_size=1, max_size=40))
def test_chunking_is_deterministic(body: str, title: str) -> None:
    a = chunk_markdown(body, title=title)
    b = chunk_markdown(body, title=title)
    assert [c.content_hash for c in a] == [c.content_hash for c in b]
    assert [c.chunk_index for c in a] == list(range(len(a)))


@given(body=_markdownish, title=st.text(min_size=1, max_size=40))
def test_chunk_indices_are_contiguous(body: str, title: str) -> None:
    chunks = chunk_markdown(body, title=title)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


@given(query=st.text(min_size=1, max_size=200))
def test_normalise_is_idempotent(query: str) -> None:
    try:
        once = normalise_query(query)
    except EmptyQueryError:
        return
    assert normalise_query(once) == once
    assert len(once) <= 512
