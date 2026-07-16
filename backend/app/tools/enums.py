"""Permissions and the shared tool/rule error taxonomy."""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    """Least-privilege capabilities a tool requires (separate from JWT auth)."""

    customer_read = "customer_read"
    order_read = "order_read"
    shipment_read = "shipment_read"
    policy_read = "policy_read"
    rules_execute = "rules_execute"
    internal_tool_inspect = "internal_tool_inspect"


class ToolErrorCode(StrEnum):
    """Stable error codes distinguishing business, security and technical failures."""

    # Expected business / user-correctable outcomes.
    not_found = "not_found"
    ambiguous_match = "ambiguous_match"
    missing_information = "missing_information"
    invalid_input = "invalid_input"
    invalid_state = "invalid_state"
    policy_not_found = "policy_not_found"
    policy_expired = "policy_expired"
    policy_conflict = "policy_conflict"
    duplicate_action = "duplicate_action"

    # Security failures.
    ownership_mismatch = "ownership_mismatch"
    forbidden = "forbidden"

    # Technical failures.
    tool_timeout = "tool_timeout"
    dependency_unavailable = "dependency_unavailable"
    internal_error = "internal_error"


# The full set of read-only permissions available in S2 (no write/execute exists).
READ_PERMISSIONS = frozenset(
    {
        Permission.customer_read,
        Permission.order_read,
        Permission.shipment_read,
        Permission.policy_read,
        Permission.rules_execute,
        Permission.internal_tool_inspect,
    }
)
