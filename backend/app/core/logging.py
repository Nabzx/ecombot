"""Structured, PII-safe logging configuration (S7).

Two formatters — a compact console line and a structured JSON record — selected by
environment. Every record passes through a redaction filter (PII + secrets) and is
enriched with the current observability context (correlation / request / trace / span
ids), so a single ticket's journey is greppable and no customer contact detail, card
number, token or secret ever reaches the logs.
"""

from __future__ import annotations

import json
import logging
from logging.config import dictConfig
from typing import Any

from app.core.context import current
from app.core.pii import redact_log

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

_configured = False

# Standard LogRecord attributes we must not re-emit as "extra" structured fields.
_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


class RedactionFilter(logging.Filter):
    """Redact PII and secrets from the message and any string extra fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_log(record.msg)
        if record.args:
            record.args = tuple(
                redact_log(a) if isinstance(a, str) else a for a in record.args
            )
        for key, value in list(record.__dict__.items()):
            if key not in _RESERVED and isinstance(value, str):
                record.__dict__[key] = redact_log(value)
        return True


class ContextFilter(logging.Filter):
    """Attach the current observability context fields to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in current().as_log_fields().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record (redaction and context already applied)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, DATE_FORMAT),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload.setdefault(key, value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=True)


def configure_logging(level: str = "INFO", *, json_logs: bool = False) -> None:
    """Configure root logging once. Safe to call multiple times.

    ``json_logs`` chooses the structured formatter; the console line is the default.
    """
    global _configured
    if _configured:
        logging.getLogger().setLevel(level)
        return

    formatter = "json" if json_logs else "default"
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "context": {"()": "app.core.logging.ContextFilter"},
                "redaction": {"()": "app.core.logging.RedactionFilter"},
            },
            "formatters": {
                "default": {"format": LOG_FORMAT, "datefmt": DATE_FORMAT},
                "json": {"()": "app.core.logging.JsonFormatter"},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": formatter,
                    "filters": ["context", "redaction"],
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {"handlers": ["console"], "level": level},
            "loggers": {
                name: {"handlers": ["console"], "level": level, "propagate": False}
                for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
            },
        }
    )
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger."""
    return logging.getLogger(name)
