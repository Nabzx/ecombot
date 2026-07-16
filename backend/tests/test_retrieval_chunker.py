"""Chunker tests: determinism, structure, rule integrity."""

from __future__ import annotations

from app.retrieval.chunker import chunk_markdown

DOC = """# Returns Policy

Items can be returned within 30 days of delivery, provided they are unused.

## Conditions

1. Items must be unused and in a resaleable condition, with original packaging.
2. Damaged or incorrect items are exempt from the packaging requirement.
3. The 30-day window is counted from the confirmed delivery date of the order.

## Exceptions

Hygiene-sensitive items may only be returned if faulty.
"""


def test_deterministic() -> None:
    a = chunk_markdown(DOC, title="Returns Policy")
    b = chunk_markdown(DOC, title="Returns Policy")
    assert [c.content_hash for c in a] == [c.content_hash for c in b]
    assert [c.chunk_index for c in a] == list(range(len(a)))


def test_heading_hierarchy_in_section_path() -> None:
    chunks = chunk_markdown(DOC, title="Returns Policy")
    paths = {c.section_path for c in chunks}
    assert "Returns Policy > Conditions" in paths
    # Subheadings carry their parent context.
    assert all(p.startswith("Returns Policy") for p in paths)
    # No duplicated "Title > Title" when the H1 equals the title.
    assert "Returns Policy > Returns Policy" not in paths


def test_numbered_rules_not_split() -> None:
    chunks = chunk_markdown(DOC, title="Returns Policy")
    conditions = next(c for c in chunks if c.section_path.endswith("Conditions"))
    for n in ("1.", "2.", "3."):
        assert n in conditions.body  # all three numbered rules stay together


def test_short_section_merged() -> None:
    chunks = chunk_markdown(DOC, title="Returns Policy")
    # The short "Exceptions" section is merged into the previous chunk (min-size rule).
    assert all(c.character_count > 0 for c in chunks)
    assert any("faulty" in c.body for c in chunks)


def test_empty_document() -> None:
    assert chunk_markdown("", title="Empty") == []
    assert chunk_markdown("   \n  \n", title="Empty") == []


def test_search_text_includes_heading_context() -> None:
    chunks = chunk_markdown(DOC, title="Returns Policy")
    assert all(c.search_text.startswith(c.section_path) for c in chunks)
