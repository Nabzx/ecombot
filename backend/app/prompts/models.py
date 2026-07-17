"""Typed prompt definitions with deterministic, immutable hashes."""

from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, computed_field

from app.llm.enums import ModelTaskType, PromptStatus

# The only templating construct: ``{{ field }}`` variable substitution. No conditionals,
# loops, attribute access or expressions, so a template can never execute code.
PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class PromptDefinition(BaseModel):
    """A single immutable prompt version loaded from a canonical YAML source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    semantic_version: str
    task_type: ModelTaskType
    status: PromptStatus = PromptStatus.DRAFT
    description: str = ""
    security_notes: str = ""

    system_template: str
    user_template: str
    input_schema_name: str
    output_schema_name: str

    required_context_fields: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    max_input_length: int = 24_000
    max_output_tokens: int = 1_024
    default_temperature: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def template_hash(self) -> str:
        """A deterministic SHA-256 over the canonical, hash-relevant content.

        Stable for identical content and independent of field ordering, so persistence
        can prove a rendered call used exactly this source version.
        """
        canonical = json.dumps(
            {
                "name": self.name,
                "semantic_version": self.semantic_version,
                "task_type": self.task_type.value,
                "system_template": self.system_template,
                "user_template": self.user_template,
                "input_schema_name": self.input_schema_name,
                "output_schema_name": self.output_schema_name,
                "required_context_fields": sorted(self.required_context_fields),
                "allowed_tools": sorted(self.allowed_tools),
                "max_input_length": self.max_input_length,
                "max_output_tokens": self.max_output_tokens,
                "default_temperature": self.default_temperature,
            },
            sort_keys=True,
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def placeholders(self) -> frozenset[str]:
        """All ``{{ field }}`` names referenced in either template."""
        names = PLACEHOLDER_RE.findall(self.system_template)
        names += PLACEHOLDER_RE.findall(self.user_template)
        return frozenset(names)
