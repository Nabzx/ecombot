"""Deterministic query preparation (no LLM).

Normalises unicode/whitespace and strips a few obvious instruction-injection wrappers,
but never removes meaning-bearing words like "not", "after", "before", "within",
"without" or "delivered". The original query is preserved separately.
"""

from __future__ import annotations

import re
import unicodedata

from app.retrieval.constants import MAX_QUERY_CHARS

# Leading injection wrappers to strip (anchored, conservative — content is kept).
_WRAPPER_RE = re.compile(
    r"^\s*(ignore (all )?previous instructions[.:,]?\s*"
    r"|system:\s*|assistant:\s*|you are now[^.]*\.\s*)",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


class EmptyQueryError(ValueError):
    """Raised when a query is empty after normalisation."""


def normalise_query(query: str) -> str:
    text = unicodedata.normalize("NFKC", query)
    # Strip up to a couple of stacked wrapper prefixes without touching the real query.
    for _ in range(3):
        stripped = _WRAPPER_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if not text:
        raise EmptyQueryError("Query is empty after normalisation")
    return text[:MAX_QUERY_CHARS]


_TOKEN_RE = re.compile(r"[a-z0-9]+")
# A tiny stop set; meaning-bearing words (not/after/before/within/without/delivered) are
# deliberately NOT removed because they change policy meaning.
_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "to",
        "of",
        "for",
        "and",
        "or",
        "my",
        "i",
        "you",
        "do",
        "can",
        "it",
        "in",
        "on",
        "at",
        "be",
        "was",
        "me",
        "we",
        "this",
    }
)


def to_or_tsquery_expr(query: str) -> str:
    """Build an OR-of-terms tsquery expression (recall-oriented) from a query.

    Returns an empty string when no usable terms remain (caller skips lexical search).
    """
    tokens = [
        t for t in _TOKEN_RE.findall(query.lower()) if len(t) > 1 and t not in _STOP
    ]
    # Deduplicate while preserving order for determinism.
    seen: dict[str, None] = {}
    for token in tokens:
        seen.setdefault(token, None)
    return " | ".join(seen)
