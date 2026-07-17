"""In-memory prompt registry: resolve prompts by name and active version."""

from __future__ import annotations

from functools import lru_cache

from app.llm.enums import ModelTaskType, PromptStatus
from app.prompts.loader import load_all_definitions
from app.prompts.models import PromptDefinition


class PromptRegistry:
    """Holds every loaded prompt version and resolves the active one per name."""

    def __init__(self, definitions: list[PromptDefinition]) -> None:
        self._by_key: dict[tuple[str, str], PromptDefinition] = {}
        self._active: dict[str, PromptDefinition] = {}
        for definition in definitions:
            key = (definition.name, definition.semantic_version)
            if key in self._by_key:
                raise ValueError(f"Duplicate prompt version: {key}")
            self._by_key[key] = definition
            if definition.status == PromptStatus.ACTIVE:
                if definition.name in self._active:
                    raise ValueError(
                        f"More than one active version for prompt {definition.name}"
                    )
                self._active[definition.name] = definition

    def all_definitions(self) -> list[PromptDefinition]:
        return [self._by_key[key] for key in sorted(self._by_key)]

    def get(self, name: str, version: str) -> PromptDefinition:
        try:
            return self._by_key[(name, version)]
        except KeyError as exc:
            raise KeyError(f"Unknown prompt {name!r} version {version!r}") from exc

    def get_active(self, name: str) -> PromptDefinition:
        """Return the explicitly-active version (never a draft/deprecated)."""
        try:
            return self._active[name]
        except KeyError as exc:
            raise KeyError(f"No active version for prompt {name!r}") from exc

    def active_for_task(self, task_type: ModelTaskType) -> PromptDefinition:
        """Return the single active prompt for a task type."""
        matches = [d for d in self._active.values() if d.task_type == task_type]
        if not matches:
            raise KeyError(f"No active prompt for task {task_type.value}")
        if len(matches) > 1:
            raise ValueError(f"Multiple active prompts for task {task_type.value}")
        return matches[0]


@lru_cache
def get_prompt_registry() -> PromptRegistry:
    """Return a cached registry loaded from the canonical template files."""
    return PromptRegistry(load_all_definitions())
