"""Load and validate prompt definitions from canonical YAML template files."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.prompts.models import PromptDefinition
from app.prompts.validation import validate_definition

TEMPLATES_DIR = Path(__file__).parent / "templates"


def load_definition_file(path: Path) -> PromptDefinition:
    """Load and validate a single ``<version>.yaml`` prompt file."""
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Prompt file {path} must contain a mapping")
    # Normalise list fields to tuples so the frozen model stays hashable.
    for key in ("required_context_fields", "allowed_tools"):
        if key in data and isinstance(data[key], list):
            data[key] = tuple(data[key])
    definition = PromptDefinition.model_validate(data)
    validate_definition(definition)
    # The on-disk directory/filename must agree with the declared identity.
    expected_task_dir = definition.task_type.value
    if path.parent.name != expected_task_dir:
        raise ValueError(
            f"Prompt {definition.name} declares task {definition.task_type.value} "
            f"but lives under {path.parent.name}/"
        )
    if path.stem != definition.semantic_version:
        raise ValueError(
            f"Prompt {definition.name} file {path.name} does not match version "
            f"{definition.semantic_version}"
        )
    return definition


def load_all_definitions(base_dir: Path = TEMPLATES_DIR) -> list[PromptDefinition]:
    """Load every prompt definition under ``base_dir`` in deterministic order."""
    definitions: list[PromptDefinition] = []
    for path in sorted(base_dir.glob("*/*.yaml")):
        definitions.append(load_definition_file(path))
    return definitions
