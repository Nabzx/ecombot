"""Provider-neutral model layer (S4).

Versioned prompts, strictly-validated structured outputs, a deterministic mock provider
(default for CI/tests) plus optional Ollama and OpenAI-compatible hosted adapters, and
five independently callable model tasks. Every model output is a *proposal*:
deterministic rules, permissions and later human approval remain authoritative.
"""
