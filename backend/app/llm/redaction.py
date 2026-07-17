"""Central redaction used before logging or persisting any model request/output.

Replaces sensitive values with consistent labels such as ``[REDACTED_EMAIL]`` while
preserving enough structure for debugging. Patterns are ordered so more specific secrets
(database URLs, JWTs) are caught before broader ones (emails). Redaction is deliberately
conservative about policy text: it targets credentials, contact details and card-like
digit runs, not ordinary prose or the ``MER-`` identifiers (which contain letters).
"""

from __future__ import annotations

import re
from typing import Any

# Ordered (pattern, replacement). Order matters: DB URLs and JWTs must run before the
# email/secret patterns that would otherwise partially match them.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:@/]+:[^\s:@/]+@[^\s/]+"),
        "[REDACTED_DB_URL]",
    ),
    (
        re.compile(r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
        "[REDACTED_JWT]",
    ),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"\b(?:sk|rk|pk|api)[-_][A-Za-z0-9]{12,}\b"), "[REDACTED_SECRET]"),
    (
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
        "[REDACTED_EMAIL]",
    ),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[REDACTED_CARD]"),
    (
        re.compile(r"(?<!\w)(?:\+44\s?7\d{3}|0\d{4})\s?\d{5,6}(?!\w)"),
        "[REDACTED_PHONE]",
    ),
    (
        re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b"),
        "[REDACTED_POSTCODE]",
    ),
)

# Dict keys whose values are secrets regardless of content.
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(password|passwd|secret|api[_-]?key|token|authorization|access[_-]?key"
    r"|private[_-]?key|jwt)"
)


def redact_text(text: str) -> str:
    """Return ``text`` with sensitive substrings replaced by labels."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_value(value: Any) -> Any:
    """Recursively redact strings inside dicts/lists; blank out sensitive keys."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SENSITIVE_KEY_RE.search(key):
                result[key] = "[REDACTED_SECRET]"
            else:
                result[key] = redact_value(item)
        return result
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_json(payload: dict[str, Any]) -> dict[str, Any]:
    """Redact a JSON-like mapping for safe persistence/logging."""
    redacted = redact_value(payload)
    assert isinstance(redacted, dict)  # noqa: S101 - narrow type for callers
    return redacted
