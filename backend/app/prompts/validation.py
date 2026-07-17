"""Structural validation of prompt definitions (independent of rendering)."""

from __future__ import annotations

from app.prompts.models import PromptDefinition


class PromptValidationError(ValueError):
    """Raised when a prompt definition is structurally invalid."""


def validate_definition(definition: PromptDefinition) -> None:
    """Validate a definition's internal consistency. Raises on any problem."""
    referenced = definition.placeholders()
    declared = set(definition.required_context_fields)

    # Every placeholder used in a template must be declared as a required context field,
    # so callers know exactly what to supply and unknown variables cannot slip in.
    undeclared = referenced - declared
    if undeclared:
        raise PromptValidationError(
            f"Prompt {definition.name} references undeclared variables: "
            f"{sorted(undeclared)}"
        )
    # Declared-but-unused fields are a source of drift; forbid them too.
    unused = declared - referenced
    if unused:
        raise PromptValidationError(
            f"Prompt {definition.name} declares unused variables: {sorted(unused)}"
        )
    name = definition.name
    if not definition.system_template.strip():
        raise PromptValidationError(f"Prompt {name} has an empty system template")
    if not definition.user_template.strip():
        raise PromptValidationError(f"Prompt {name} has an empty user template")
    if definition.max_input_length <= 0:
        raise PromptValidationError(f"Prompt {name} max_input_length must be > 0")
    if definition.max_output_tokens <= 0:
        raise PromptValidationError(
            f"Prompt {definition.name} max_output_tokens must be > 0"
        )
