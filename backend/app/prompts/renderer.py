"""Safe prompt rendering: variable substitution only, no code execution.

Rendering fails when a required variable is missing and rejects unknown variables.
Values are inserted verbatim as data — customer content is never concatenated into
system instructions, only placed inside the untrusted blocks the template defines.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.prompts.models import PLACEHOLDER_RE, PromptDefinition


class PromptRenderError(ValueError):
    """Raised when a prompt cannot be rendered from the supplied context."""


@dataclass(frozen=True)
class RenderedPrompt:
    """The result of rendering a prompt definition against a context."""

    system_message: str
    user_message: str
    prompt_name: str
    prompt_version: str
    template_hash: str


def render_prompt(
    definition: PromptDefinition, context: dict[str, object]
) -> RenderedPrompt:
    """Render ``definition`` with ``context``. Raises on missing/unknown variables."""
    required = set(definition.required_context_fields)
    provided = set(context)

    missing = required - provided
    if missing:
        raise PromptRenderError(
            f"Prompt {definition.name} missing required variables: {sorted(missing)}"
        )
    unknown = provided - required
    if unknown:
        raise PromptRenderError(
            f"Prompt {definition.name} received unknown variables: {sorted(unknown)}"
        )

    def substitute(template: str) -> str:
        def replace(match: object) -> str:
            key = match.group(1)  # type: ignore[attr-defined]
            return str(context[key])

        return PLACEHOLDER_RE.sub(replace, template)

    return RenderedPrompt(
        system_message=substitute(definition.system_template),
        user_message=substitute(definition.user_template),
        prompt_name=definition.name,
        prompt_version=definition.semantic_version,
        template_hash=definition.template_hash,
    )
