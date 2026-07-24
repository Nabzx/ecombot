"""Observability context: one traceable identity per request / run / job (S7).

A single ``contextvars``-based context carries the correlation, request, trace and span
ids (and the authenticated actor) through async call chains without threading arguments
everywhere. The API middleware, the workflow runner and the outbox worker all bind this
context so a ticket's whole journey — API call, workflow steps, approval, outbox job and
execution — shares one correlation id.

Nothing here is ever taken from untrusted request bodies; header-supplied ids are
length- and charset-validated before they are adopted.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Any

# Ids are opaque, short, url-safe tokens. We accept inbound header values only if they
# match this shape, otherwise we mint a fresh one (never trust arbitrary header text).
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class ObservabilityContext:
    correlation_id: str
    request_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    actor_user_id: str | None = None
    actor_role: str | None = None

    def as_log_fields(self) -> dict[str, str]:
        fields = {"correlation_id": self.correlation_id}
        if self.request_id:
            fields["request_id"] = self.request_id
        if self.trace_id:
            fields["trace_id"] = self.trace_id
        if self.span_id:
            fields["span_id"] = self.span_id
        if self.actor_role:
            fields["actor_role"] = self.actor_role
        return fields


_EMPTY = ObservabilityContext(correlation_id="-")
_CTX: ContextVar[ObservabilityContext] = ContextVar("observability_ctx", default=_EMPTY)


def new_id(prefix: str = "") -> str:
    token = uuid.uuid4().hex[:16]
    return f"{prefix}{token}" if prefix else token


def sanitise_id(value: str | None) -> str | None:
    """Return the value only if it is a safe id shape, else None."""
    if value is None:
        return None
    value = value.strip()
    return value if _ID_RE.match(value) else None


def current() -> ObservabilityContext:
    return _CTX.get()


def get_correlation_id() -> str:
    return _CTX.get().correlation_id


def bind(context: ObservabilityContext) -> Token[ObservabilityContext]:
    """Replace the current context; returns a token for :func:`reset`."""
    return _CTX.set(context)


def reset(token: Token[ObservabilityContext]) -> None:
    _CTX.reset(token)


def update(**changes: str | None) -> Token[ObservabilityContext]:
    """Bind a copy of the current context with fields overridden."""
    current_ctx: Any = _CTX.get()
    return _CTX.set(replace(current_ctx, **changes))


@contextmanager
def use(context: ObservabilityContext) -> Iterator[ObservabilityContext]:
    token = bind(context)
    try:
        yield context
    finally:
        reset(token)


@contextmanager
def correlation(
    correlation_id: str | None = None, **fields: str | None
) -> Iterator[ObservabilityContext]:
    """Enter a context with the given (or a fresh) correlation id and extra fields."""
    context = ObservabilityContext(
        correlation_id=correlation_id or new_id("cor-"), **fields
    )
    with use(context) as ctx:
        yield ctx
