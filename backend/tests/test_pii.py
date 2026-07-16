"""PII masking and redaction tests."""

from __future__ import annotations

from app.core.pii import mask_email, mask_phone, redact_pii


def test_mask_email() -> None:
    assert mask_email("jane.doe@example.com") == "j***@example.com"
    assert mask_email("a@example.com") == "*@example.com"
    assert mask_email("notanemail") == "***"


def test_mask_phone() -> None:
    assert mask_phone("07911123456") == "*******3456"
    assert mask_phone("123") == "****"


def test_redact_pii_in_free_text() -> None:
    text = (
        "Contact jane.doe@example.com or call 07911123456 about order MER-2026-000001."
    )
    redacted = redact_pii(text)
    assert "jane.doe@example.com" not in redacted
    assert "07911123456" not in redacted
    assert "j***@example.com" in redacted
    assert "3456" in redacted
    # Non-PII content is preserved.
    assert "MER-2026-000001" in redacted
