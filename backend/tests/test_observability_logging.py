"""Observability context, redaction and structured-logging tests (S7)."""

from __future__ import annotations

import json
import logging

from app.core.context import (
    ObservabilityContext,
    correlation,
    current,
    get_correlation_id,
    new_id,
    sanitise_id,
    use,
)
from app.core.logging import ContextFilter, JsonFormatter, RedactionFilter
from app.core.pii import redact_log, redact_secrets


# --- context ------------------------------------------------------------------------
def test_context_defaults_and_binding() -> None:
    assert get_correlation_id() == "-"
    with correlation("cor-abc", request_id="req-1", actor_role="supervisor") as ctx:
        assert ctx.correlation_id == "cor-abc"
        assert current().request_id == "req-1"
        assert current().as_log_fields()["actor_role"] == "supervisor"
    # Context is restored on exit.
    assert get_correlation_id() == "-"


def test_nested_context_restores() -> None:
    with use(ObservabilityContext(correlation_id="outer")):
        assert get_correlation_id() == "outer"
        with use(ObservabilityContext(correlation_id="inner")):
            assert get_correlation_id() == "inner"
        assert get_correlation_id() == "outer"


def test_sanitise_id_rejects_untrusted() -> None:
    assert sanitise_id("req-123_ABC.def") == "req-123_ABC.def"
    assert sanitise_id("bad value with spaces") is None
    assert sanitise_id("x" * 65) is None
    assert sanitise_id("<script>") is None
    assert sanitise_id(None) is None
    assert new_id("cor-").startswith("cor-")


# --- redaction ----------------------------------------------------------------------
def test_redaction_masks_pii_and_secrets() -> None:
    text = (
        "email jane.doe@example.com phone 07911123456 card 4111 1111 1111 1111 "
        "password=hunter2 token: abcd.efgh.ijkl "
        "jwt eyJhbGciOi.JSUzI1NiIsInR5.cCI6IkpXVCJ9 "
        "Authorization: Bearer sk-secret-value"
    )
    out = redact_log(text)
    assert "jane.doe@example.com" not in out
    assert "07911123456" not in out
    assert "4111 1111 1111 1111" not in out
    assert "hunter2" not in out
    assert "sk-secret-value" not in out
    assert "eyJhbGciOi.JSUzI1NiIsInR5.cCI6IkpXVCJ9" not in out
    assert "[REDACTED_JWT]" in out


def test_redact_secrets_keeps_card_last_four() -> None:
    assert redact_secrets("card 4111111111111111 end").count("*") >= 12
    assert "1111 end" in redact_secrets("card 4111111111111111 end")


# --- logging filters + formatter ----------------------------------------------------
def _record(msg: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_redaction_filter_scrubs_record() -> None:
    record = _record("contact jane.doe@example.com now", customer="a@b.com")
    RedactionFilter().filter(record)
    assert "jane.doe@example.com" not in record.getMessage()
    assert record.customer != "a@b.com"  # type: ignore[attr-defined]


def test_context_filter_and_json_formatter() -> None:
    with correlation("cor-xyz", request_id="req-9"):
        record = _record("approval_granted", approval_id="ap-1")
        ContextFilter().filter(record)
        RedactionFilter().filter(record)
        line = JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["event"] == "approval_granted"
    assert payload["correlation_id"] == "cor-xyz"
    assert payload["request_id"] == "req-9"
    assert payload["approval_id"] == "ap-1"
    assert payload["level"] == "INFO"
