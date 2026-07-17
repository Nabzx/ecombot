"""Redaction tests: secrets/PII removed, structure and identifiers preserved."""

from __future__ import annotations

import pytest
from app.llm.redaction import redact_json, redact_text

SECRETS = [
    ("jane.doe@example.com", "[REDACTED_EMAIL]"),
    ("07911123456", "[REDACTED_PHONE]"),
    ("4111 1111 1111 1111", "[REDACTED_CARD]"),
    ("postgresql://user:pass@host:5432/db", "[REDACTED_DB_URL]"),
    ("sk-ABCDEF1234567890XYZ", "[REDACTED_SECRET]"),
    ("eyJhbGci.eyJzdWIi.SflKxwRJ", "[REDACTED_JWT]"),
    ("SW1A 1AA", "[REDACTED_POSTCODE]"),
]


@pytest.mark.parametrize(("secret", "label"), SECRETS)
def test_secret_is_redacted(secret: str, label: str) -> None:
    out = redact_text(f"value is {secret} end")
    assert secret not in out
    assert label in out


def test_bearer_token_redacted() -> None:
    out = redact_text("Authorization: Bearer abcdef123456789")
    assert "abcdef123456789" not in out
    assert "[REDACTED_TOKEN]" in out


def test_mer_identifiers_preserved() -> None:
    text = "order MER-2026-000123 sku MER-DEC-001 tracking MER-TRK-00000007"
    assert redact_text(text) == text


def test_sensitive_keys_blanked() -> None:
    payload = {
        "password": "hunter2",
        "api_key": "zzz",
        "note": "contact me@x.com",
        "safe": "MER-2026-000001",
    }
    out = redact_json(payload)
    assert out["password"] == "[REDACTED_SECRET]"
    assert out["api_key"] == "[REDACTED_SECRET]"
    assert "me@x.com" not in out["note"]
    assert out["safe"] == "MER-2026-000001"


def test_nested_structures_redacted() -> None:
    payload = {"outer": {"list": ["email a@b.com", "ok"], "token": "abc"}}
    out = redact_json(payload)
    assert "a@b.com" not in out["outer"]["list"][0]
    assert out["outer"]["token"] == "[REDACTED_SECRET]"


def test_policy_text_not_over_redacted() -> None:
    text = "Returns are accepted within 30 days of delivery."
    assert redact_text(text) == text
