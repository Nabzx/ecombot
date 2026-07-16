"""The typed tool result envelope."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, SerializeAsAny

from app.rules.enums import RiskLevel
from app.tools.errors import ToolError

DataT = TypeVar("DataT")


class ToolMetadata(BaseModel):
    risk_level: RiskLevel
    tool_version: str
    duration_ms: int
    correlation_id: str


class ToolResult(BaseModel, Generic[DataT]):
    ok: bool
    tool: str
    # SerializeAsAny so the concrete data model's fields are emitted, not just the
    # declared BaseModel's (which would serialise as ``{}``).
    data: SerializeAsAny[DataT] | None = None
    error: ToolError | None = None
    metadata: ToolMetadata
