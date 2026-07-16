"""PII masking helpers.

Used by summary schemas now and by PII-aware logging in a later stage. These never
mutate stored data — they only produce masked display strings.
"""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\b0\d{9,10}\b")


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
