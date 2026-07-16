"""Deterministic business-rules engine (S2).

The authoritative, LLM-independent layer that decides ownership, eligibility, limits,
risk and routing. Rules are pure functions over typed inputs and an injected clock;
they return ``RuleResult`` values rather than raising for ordinary outcomes.
"""
