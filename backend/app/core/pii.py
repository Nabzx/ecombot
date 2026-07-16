"""PII masking helpers.

Used by summary schemas now and by PII-aware logging in a later stage. These never
mutate stored data — they only produce masked display strings.
"""

from __future__ import annotations


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
