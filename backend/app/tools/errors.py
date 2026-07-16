"""Typed tool errors and the exception handlers convert into them.

Ordinary business outcomes are *not* exceptions — handlers return data or raise
``ToolFailure`` with a stable code. The executor turns any other exception into a safe
``internal_error`` without leaking a stack trace.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.tools.enums import ToolErrorCode


class ToolError(BaseModel):
    code: ToolErrorCode
    message: str
    retryable: bool = False


class ToolFailure(Exception):  # noqa: N818 - paired with the ToolError model, not "*Error"
    """Raise inside a tool handler to return a typed error from the executor."""

    def __init__(
        self, code: ToolErrorCode, message: str, *, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def to_error(self) -> ToolError:
        return ToolError(code=self.code, message=self.message, retryable=self.retryable)


# Convenience constructors for the common cases.
def not_found(message: str) -> ToolFailure:
    return ToolFailure(ToolErrorCode.not_found, message)


def forbidden(message: str) -> ToolFailure:
    return ToolFailure(ToolErrorCode.forbidden, message)


def ownership_mismatch(message: str) -> ToolFailure:
    return ToolFailure(ToolErrorCode.ownership_mismatch, message)


def invalid_input(message: str) -> ToolFailure:
    return ToolFailure(ToolErrorCode.invalid_input, message)
