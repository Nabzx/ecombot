"""Versioned, hashable, immutable prompt registry (S4).

Prompts live in ``templates/<task>/<version>.yaml`` — version controlled, human
readable and inspectable in GitHub — never buried inside service methods. Each
definition separates trusted system instructions from untrusted customer content with
labelled data blocks and carries a deterministic hash so what was rendered can be
verified against source.
"""

from app.prompts.models import PromptDefinition
from app.prompts.registry import PromptRegistry, get_prompt_registry

__all__ = ["PromptDefinition", "PromptRegistry", "get_prompt_registry"]
