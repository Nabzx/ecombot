"""Model-layer enums: tasks, proposed actions, error taxonomy, capabilities, statuses.

These are the stable vocabulary of the S4 model layer. Values are wire-stable strings so
they can appear in persisted model-call records, evaluation datasets and API payloads.
"""

from __future__ import annotations

from enum import StrEnum


class ModelTaskType(StrEnum):
    """The independently callable model tasks. S5 will decide when they run."""

    TICKET_CLASSIFICATION = "ticket_classification"
    IDENTIFIER_EXTRACTION = "identifier_extraction"
    READ_ONLY_TOOL_PLANNING = "read_only_tool_planning"
    EVIDENCE_SUMMARY = "evidence_summary"
    RESPONSE_DRAFTING = "response_drafting"
    DECISION_SUMMARY = "decision_summary"
    STRUCTURED_OUTPUT_REPAIR = "structured_output_repair"


class ProposedAction(StrEnum):
    """Actions the model may *propose*. Deliberately contains no execute_* values.

    Execution, approval and record mutation are never model-facing. The
    response-drafting task is additionally constrained to an allowed-action subset
    derived from deterministic rule results at call time.
    """

    PROVIDE_TRACKING_INFORMATION = "provide_tracking_information"
    PROVIDE_POLICY_INFORMATION = "provide_policy_information"
    REQUEST_MORE_INFORMATION = "request_more_information"
    OFFER_RETURN_AUTHORISATION = "offer_return_authorisation"
    PROPOSE_REPLACEMENT = "propose_replacement"
    REQUEST_SUPERVISOR_REFUND_APPROVAL = "request_supervisor_refund_approval"
    REQUEST_SUPERVISOR_CANCELLATION_APPROVAL = (
        "request_supervisor_cancellation_approval"
    )
    ESCALATE_TO_SUPPORT_AGENT = "escalate_to_support_agent"
    ESCALATE_TO_SUPERVISOR = "escalate_to_supervisor"
    NO_ACTION = "no_action"


class ModelErrorCode(StrEnum):
    """Stable typed error taxonomy. Raw provider exceptions never reach consumers."""

    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_AUTHENTICATION_FAILED = "provider_authentication_failed"
    INVALID_PROVIDER_CONFIGURATION = "invalid_provider_configuration"
    INVALID_STRUCTURED_OUTPUT = "invalid_structured_output"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    OUTPUT_REPAIR_FAILED = "output_repair_failed"
    INPUT_TOO_LONG = "input_too_long"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    CONTENT_FILTERED = "content_filtered"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    INTERNAL_ERROR = "internal_error"


# Error codes for which trying a *different* provider (fallback) is appropriate. These
# are transient/infrastructure failures, not application, config or safety failures.
RETRYABLE_ERROR_CODES: frozenset[ModelErrorCode] = frozenset(
    {
        ModelErrorCode.PROVIDER_UNAVAILABLE,
        ModelErrorCode.PROVIDER_TIMEOUT,
        ModelErrorCode.PROVIDER_RATE_LIMITED,
        ModelErrorCode.DEPENDENCY_UNAVAILABLE,
    }
)

# Error codes that must NEVER trigger fallback or retry — retrying would either repeat a
# deterministic failure or violate policy (e.g. a content-filter decision).
NON_RETRYABLE_ERROR_CODES: frozenset[ModelErrorCode] = frozenset(
    {
        ModelErrorCode.PROVIDER_AUTHENTICATION_FAILED,
        ModelErrorCode.INVALID_PROVIDER_CONFIGURATION,
        ModelErrorCode.INPUT_TOO_LONG,
        ModelErrorCode.UNSUPPORTED_CAPABILITY,
        ModelErrorCode.CONTENT_FILTERED,
    }
)


class ProviderCapability(StrEnum):
    """Capabilities a provider declares. Service branches on capability, not name."""

    NATIVE_STRUCTURED_OUTPUTS = "native_structured_outputs"
    JSON_MODE = "json_mode"
    TOOL_CALLING = "tool_calling"
    STREAMING = "streaming"
    TOKEN_USAGE = "token_usage"  # noqa: S105 - capability name, not a secret
    REQUEST_IDS = "request_ids"
    SYSTEM_MESSAGES = "system_messages"
    TEMPERATURE = "temperature"
    SEED = "seed"


class FinishReason(StrEnum):
    """Why generation stopped, normalised across providers."""

    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    UNKNOWN = "unknown"


class ModelCallStatus(StrEnum):
    """Terminal status of a model call, persisted on the model_calls row."""

    SUCCEEDED = "succeeded"
    REPAIRED = "repaired"
    FAILED = "failed"


class TokenUsageSource(StrEnum):
    """Provenance of a token count — never claim exact figures for an estimate."""

    PROVIDER_REPORTED = "provider_reported"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class CostStatus(StrEnum):
    """Provenance of a cost figure."""

    PROVIDER_REPORTED = "provider_reported"
    ESTIMATED = "estimated"
    ZERO_LOCAL = "zero_local"
    ZERO_MOCK = "zero_mock"
    UNKNOWN = "unknown"


class PromptStatus(StrEnum):
    """Lifecycle status of a prompt version."""

    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class TrustLevel(StrEnum):
    """Trust label for a prompt data block. Representational, not a complete defence."""

    SYSTEM = "system"
    DETERMINISTIC = "deterministic"
    OFFICIAL = "official"
    UNTRUSTED = "untrusted"
