"""PII masking helpers.

Used by summary schemas now and by PII-aware logging in a later stage. These never
mutate stored data — they only produce masked display strings.
"""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\b0\d{9,10}\b")
# Long card-like digit runs (13-19 digits, optionally space/dash separated). Anchored to
# start and end on a digit so a trailing separator is never consumed.
_CARD_RE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
# JWTs (three base64url segments) and bearer tokens / obvious secret assignments.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]+")
_SECRET_KV_RE = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key|authorization)\b\s*[=:]\s*\S+"
)


def mask_email(email: str) -> str:
    """``jane.doe@example.com`` -> ``j***@example.com``."""
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    masked_local = local[0] + "***" if len(local) > 1 else "*"
    return f"{masked_local}@{domain}"


def mask_phone(phone: str) -> str:
    """Keep only the last four digits: ``07911 123456`` -> ``*******3456``."""
    digits = [c for c in phone if c.isdigit()]
    if len(digits) <= 4:
        return "****"
    return "*" * (len(digits) - 4) + "".join(digits[-4:])


def redact_pii(text: str) -> str:
    """Mask email addresses and UK phone numbers found anywhere in free text.

    Used for PII-safe logging so customer message bodies and contact details never reach
    logs verbatim.
    """
    text = _EMAIL_RE.sub(lambda m: mask_email(m.group()), text)
    return _PHONE_RE.sub(lambda m: mask_phone(m.group()), text)


def redact_secrets(text: str) -> str:
    """Remove card numbers, JWTs, bearer tokens and secret assignments from text."""
    text = _JWT_RE.sub("[REDACTED_JWT]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _SECRET_KV_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    return _CARD_RE.sub(lambda m: mask_card(m.group()), text)


def mask_card(card: str) -> str:
    """Keep only the last four digits of a card-like number."""
    digits = [c for c in card if c.isdigit()]
    if len(digits) <= 4:
        return "****"
    return "*" * (len(digits) - 4) + "".join(digits[-4:])


def redact_log(text: str) -> str:
    """Full PII + secret redaction for anything about to be logged."""
    return redact_secrets(redact_pii(text))
