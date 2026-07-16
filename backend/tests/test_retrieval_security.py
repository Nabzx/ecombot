"""Security tests for the model-facing retrieval surface."""

from __future__ import annotations

import pytest
from app.retrieval.constants import MAX_EXCERPT_CHARS, MAX_QUERY_CHARS
from app.tools.retrieval import SearchPoliciesInput, SearchPoliciesResult
from pydantic import ValidationError


def test_model_facing_input_has_no_source_override() -> None:
    fields = set(SearchPoliciesInput.model_fields)
    # No way for a model-facing caller to widen source types or enable historical mode.
    assert "source_type" not in fields
    assert "source_types" not in fields
    assert "include_historical" not in fields
    assert fields == {"query", "topic", "top_k"}


def test_long_query_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchPoliciesInput(query="x" * (MAX_QUERY_CHARS + 1))


def test_top_k_bounded() -> None:
    with pytest.raises(ValidationError):
        SearchPoliciesInput(query="return window", top_k=1000)


def test_result_has_no_raw_vectors_or_internal_ids() -> None:
    schema_text = str(SearchPoliciesResult.model_json_schema())
    assert "embedding" not in schema_text
    assert "search_vector" not in schema_text
    assert "chunk_id" not in schema_text
    assert "policy_version_id" not in schema_text


def test_excerpt_length_capped() -> None:
    # The citation model caps excerpts; verify the documented limit is bounded.
    assert MAX_EXCERPT_CHARS <= 600
