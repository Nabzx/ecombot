"""Deterministic evidence-support checks.

This is NOT natural-language entailment. It only reports whether current, sufficiently
relevant evidence was retrieved under configured thresholds; a human or a later grounded
generation step must still decide the final answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.constants import SEMANTIC_SUPPORT_MIN
from app.retrieval.fusion import FusedCandidate
from app.retrieval.models import SupportStatus


@dataclass(frozen=True, slots=True)
class SupportInputs:
    evidence: list[FusedCandidate]
    min_lexical_score: float
    topic_known: bool
    topic_has_active_policy: bool
    conflict: bool
    required_policy_ids: frozenset[str]
    represented_policy_ids: frozenset[str]


def determine_support(inputs: SupportInputs) -> SupportStatus:
    if inputs.conflict:
        return SupportStatus.conflicting
    if inputs.topic_known and not inputs.topic_has_active_policy:
        return SupportStatus.no_active_policy
    if not inputs.evidence:
        return SupportStatus.unsupported

    strong = any(
        (fc.lexical_score is not None and fc.lexical_score >= inputs.min_lexical_score)
        or (fc.semantic_score is not None and fc.semantic_score >= SEMANTIC_SUPPORT_MIN)
        for fc in inputs.evidence
    )
    if not strong:
        return SupportStatus.unsupported

    if inputs.required_policy_ids and not inputs.required_policy_ids.issubset(
        inputs.represented_policy_ids
    ):
        return SupportStatus.partially_supported
    return SupportStatus.supported
