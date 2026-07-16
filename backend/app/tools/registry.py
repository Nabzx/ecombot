"""Typed, inspectable tool registry.

Every tool is described by a ``ToolDefinition`` (I/O models, permission, risk, version,
retry/timeout metadata and a handler). The registry can be listed and its JSON schemas
printed without executing anything. Future write tools are listed as reserved names with
no handler so they cannot run in S2.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from pydantic import BaseModel

from app.rules.enums import RiskLevel
from app.tools.context import ToolContext
from app.tools.enums import Permission

# The handler's params are runtime-validated against ``input_model`` before the call, so
# the registry stores them type-erased (Any) — this is the one deliberate Any here.
ToolHandler = Callable[[ToolContext, Any], Awaitable[BaseModel]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int = 0  # pure rules: 0; repository reads: 1 for transient DB errors


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    permission: Permission
    risk_level: RiskLevel
    read_only: bool
    approval_required: bool
    version: str
    model_accessible: bool
    timeout_ms: int = 5_000
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    handler: ToolHandler | None = None  # None => reserved (not executable in S2)

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def output_schema(self) -> dict[str, Any]:
        return self.output_model.model_json_schema()


# Future write/execution tools — reserved names only, deliberately without handlers.
RESERVED_TOOL_NAMES = (
    "create_approval_request",
    "update_ticket_status",
    "execute_simulated_refund",
    "execute_simulated_cancellation",
    "record_audit_event",
)


@lru_cache
def get_registry() -> dict[str, ToolDefinition]:
    """Build the registry by collecting each tool module's ``TOOLS`` tuple."""
    from app.tools import (
        customers,
        orders,
        policies,
        retrieval,
        rules,
        shipments,
    )

    definitions: list[ToolDefinition] = [
        *customers.TOOLS,
        *orders.TOOLS,
        *shipments.TOOLS,
        *policies.TOOLS,
        *retrieval.TOOLS,
        *rules.TOOLS,
    ]
    registry = {definition.name: definition for definition in definitions}
    if len(registry) != len(definitions):
        raise RuntimeError("Duplicate tool name in registry")
    return registry


def list_tools() -> list[ToolDefinition]:
    return sorted(get_registry().values(), key=lambda d: d.name)


def get_tool(name: str) -> ToolDefinition | None:
    return get_registry().get(name)


def is_reserved(name: str) -> bool:
    return name in RESERVED_TOOL_NAMES
