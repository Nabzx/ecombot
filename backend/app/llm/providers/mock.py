"""Deterministic mock provider — the default for CI, tests and offline evaluation.

It is **not** a language model and never pretends to be one. It reads a compact,
machine-readable ``mock_payload`` (JSON in ``request.trace_metadata``) that the task
layer fills with the minimal structured facts needed, and synthesises a valid structured
output by explicit rules — classification by keyword, identifiers by regex, everything
else by faithfully reflecting the supplied deterministic context. Real providers ignore
``trace_metadata`` entirely.

Failure and malformed-output scenarios are injected via
``trace_metadata["mock_scenario"]`` so the parsing, repair, retry and fallback machinery
can be exercised deterministically.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.llm.cost import estimate_tokens, zero_cost
from app.llm.enums import (
    FinishReason,
    ModelErrorCode,
    ModelTaskType,
    ProviderCapability,
    TokenUsageSource,
)
from app.llm.models import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    TokenUsage,
)

MOCK_PROVIDER_NAME = "mock"
MOCK_MODEL_NAME = "mock-deterministic-v1"

# --- deterministic identifier patterns (match the S1 seed formats) -----------------
_ORDER_RE = re.compile(r"MER-2026-\d{6}")
_TRACKING_RE = re.compile(r"MER-TRK-\d{8}")
_CUSTOMER_REF_RE = re.compile(r"MER-C-\d{5}")
_SKU_RE = re.compile(r"MER-[A-Z]{2,4}-\d{3}\b")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# --- classification keyword rules (most specific first) ----------------------------
# The mock only keyword-matches customer text; instructions embedded in that text can
# never change the outcome, so it is inherently prompt-injection resistant.
_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cancellation_request", ("cancel", "stop the order", "don't ship")),
    ("refund_request", ("refund", "money back", "reimburse", "my money")),
    ("return_request", ("return", "send it back", "send this back")),
    ("damaged_item", ("damaged", "broken", "cracked", "shattered", "smashed")),
    (
        "incorrect_item",
        ("wrong item", "incorrect item", "not what i ordered", "wrong product"),
    ),
    (
        "missing_delivery",
        (
            "never arrived",
            "not arrived",
            "didn't arrive",
            "missing",
            "didn't receive",
            "hasn't arrived",
            "lost",
        ),
    ),
    (
        "delayed_delivery",
        ("delayed", "late", "still waiting", "hasn't shipped", "taking too long"),
    ),
    ("order_tracking", ("track", "tracking", "where is my order", "order status")),
    (
        "product_policy_question",
        ("policy", "how long do i have", "am i allowed", "do you accept", "warranty"),
    ),
)


class MockProvider:
    """A deterministic, offline, fixture/rule-driven provider (default everywhere)."""

    provider_name = MOCK_PROVIDER_NAME

    def __init__(self, model: str = MOCK_MODEL_NAME) -> None:
        self._model = model

    @property
    def default_model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ProviderCapabilities:
        # The mock supports strict JSON output and always reports deterministic usage.
        return ProviderCapabilities(
            capabilities=frozenset(
                {
                    ProviderCapability.NATIVE_STRUCTURED_OUTPUTS,
                    ProviderCapability.JSON_MODE,
                    ProviderCapability.TOKEN_USAGE,
                    ProviderCapability.SYSTEM_MESSAGES,
                    ProviderCapability.TEMPERATURE,
                    ProviderCapability.SEED,
                }
            )
        )

    def is_available(self) -> bool:
        return True

    async def generate(self, request: ModelRequest) -> ModelResponse:
        scenario = request.trace_metadata.get("mock_scenario", "")
        self._maybe_fail(scenario)

        payload = self._load_payload(request)
        result = self._synthesise(request.task_type, payload)
        raw_text = self._encode(result, scenario, request.task_type)

        # Deterministic latency estimate from input size (never a real wall clock).
        latency_ms = 5 + (len(request.user_message) % 20)
        usage = TokenUsage(
            input_tokens=estimate_tokens(request.system_message + request.user_message),
            output_tokens=estimate_tokens(raw_text),
            source=TokenUsageSource.PROVIDER_REPORTED,
        )
        # Parsed output is only attached when the encoded text is genuinely valid JSON,
        # so malformed scenarios exercise the service-side extraction/repair path.
        parsed = self._safe_parse(raw_text)
        return ModelResponse(
            success=True,
            raw_text=raw_text,
            parsed_output=parsed,
            provider_name=self.provider_name,
            model_name=self._model,
            task_type=request.task_type,
            token_usage=usage,
            cost=zero_cost(mock=True),
            latency_ms=latency_ms,
            finish_reason=FinishReason.STOP,
            provider_request_id=f"mock-{request.correlation_id}",
            retry_count=0,
        )

    # -- failure injection ----------------------------------------------------------
    @staticmethod
    def _maybe_fail(scenario: str) -> None:
        mapping = {
            "timeout": ModelErrorCode.PROVIDER_TIMEOUT,
            "unavailable": ModelErrorCode.PROVIDER_UNAVAILABLE,
            "rate_limited": ModelErrorCode.PROVIDER_RATE_LIMITED,
            "auth_failed": ModelErrorCode.PROVIDER_AUTHENTICATION_FAILED,
        }
        code = mapping.get(scenario)
        if code is not None:
            raise ModelProviderError(
                code, f"injected mock scenario: {scenario}", provider=MOCK_PROVIDER_NAME
            )

    # -- payload / encoding ---------------------------------------------------------
    @staticmethod
    def _load_payload(request: ModelRequest) -> dict[str, Any]:
        raw = request.trace_metadata.get("mock_payload")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _encode(
        self, result: dict[str, Any], scenario: str, task: ModelTaskType
    ) -> str:
        """Serialise the synthesised result, applying malformed-output scenarios."""
        # The repair task only re-fails when explicitly told to.
        if task == ModelTaskType.STRUCTURED_OUTPUT_REPAIR:
            if scenario == "repair_fail":
                return "{ not valid json"
            return json.dumps(result, sort_keys=True)

        if scenario == "malformed_json":
            return "Sure! Here is the answer: {not-json at all"
        if scenario == "markdown_fenced":
            return f"```json\n{json.dumps(result, sort_keys=True)}\n```"
        if scenario in {"missing_field", "repair_ok"}:
            # Drop a required field so validation fails and repair is triggered.
            corrupted = dict(result)
            for key in ("category", "customer_intent", "body", "summary", "tool_calls"):
                if key in corrupted:
                    del corrupted[key]
                    break
            return json.dumps(corrupted, sort_keys=True)
        return json.dumps(result, sort_keys=True)

    @staticmethod
    def _safe_parse(raw_text: str) -> dict[str, Any] | None:
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    # -- per-task synthesis ---------------------------------------------------------
    def _synthesise(
        self, task: ModelTaskType, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if task == ModelTaskType.TICKET_CLASSIFICATION:
            return self._classify(payload)
        if task == ModelTaskType.IDENTIFIER_EXTRACTION:
            return self._extract_identifiers(payload)
        if task == ModelTaskType.READ_ONLY_TOOL_PLANNING:
            return self._plan_tools(payload)
        if task == ModelTaskType.EVIDENCE_SUMMARY:
            return self._summarise_evidence(payload)
        if task == ModelTaskType.RESPONSE_DRAFTING:
            return self._draft_response(payload)
        if task == ModelTaskType.DECISION_SUMMARY:
            return self._decision_summary(payload)
        if task == ModelTaskType.STRUCTURED_OUTPUT_REPAIR:
            return self._repair(payload)
        raise ModelProviderError(  # pragma: no cover - defensive
            ModelErrorCode.INTERNAL_ERROR,
            f"mock has no handler for task {task}",
            provider=MOCK_PROVIDER_NAME,
        )

    def _classify(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("customer_text", "")).lower()
        allowed = set(payload.get("allowed_categories", []))
        matches: list[str] = []
        for category, keywords in _CATEGORY_RULES:
            if allowed and category not in allowed:
                continue
            if any(kw in text for kw in keywords):
                matches.append(category)
        if not matches:
            return {
                "category": "unknown",
                "confidence": 0.35,
                "alternative_categories": [],
                "requires_clarification": True,
                "missing_information": ["Unable to determine the request type."],
                "decision_summary": "The message does not clearly match a category.",
            }
        primary = matches[0]
        alternatives = [{"category": c, "confidence": 0.25} for c in matches[1:3]]
        return {
            "category": primary,
            "confidence": 0.9,
            "alternative_categories": alternatives,
            "requires_clarification": False,
            "missing_information": [],
            "decision_summary": (
                f"The message matches the {primary.replace('_', ' ')} category."
            ),
        }

    def _extract_identifiers(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("customer_text", ""))

        def field(pattern: re.Pattern[str]) -> dict[str, Any] | None:
            match = pattern.search(text)
            if not match:
                return None
            return {"value": match.group(0), "source": "explicit", "confidence": 0.99}

        order = field(_ORDER_RE)
        email = field(_EMAIL_RE)
        tracking = field(_TRACKING_RE)
        customer_ref = field(_CUSTOMER_REF_RE)
        skus = [m.group(0) for m in _SKU_RE.finditer(text)]
        return {
            "customer_email": email,
            "customer_reference": customer_ref,
            "order_number": order,
            "tracking_number": tracking,
            "product_skus": skus,
            "ambiguities": [],
        }

    def _plan_tools(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = list(payload.get("allowed_tools", []))
        category = str(payload.get("category", "unknown"))
        order_number = payload.get("known_order_number")
        email = payload.get("known_email")
        max_calls = int(payload.get("max_tool_calls", 4))

        calls: list[dict[str, Any]] = []

        def add(tool: str, arguments: dict[str, Any], purpose: str) -> None:
            if tool in allowed and not any(c["tool"] == tool for c in calls):
                calls.append({"tool": tool, "arguments": arguments, "purpose": purpose})

        if order_number:
            add("get_order", {"order_number": order_number}, "Look up the order.")
        elif email:
            add("search_customer", {"email": email}, "Find the customer by email.")
        tracking_categories = {"order_tracking", "delayed_delivery", "missing_delivery"}
        if category in tracking_categories and order_number:
            add(
                "get_shipment_status",
                {"order_number": order_number},
                "Check the shipment status.",
            )
        if category == "product_policy_question":
            add(
                "search_policies",
                {"query": str(payload.get("customer_text", ""))[:120]},
                "Find the relevant policy.",
            )
        calls = calls[:max_calls]
        return {
            "tool_calls": calls,
            "requires_more_information": not calls,
            "missing_information": (
                [] if calls else ["No order number or email to look up."]
            ),
        }

    @staticmethod
    def _summarise_evidence(payload: dict[str, Any]) -> dict[str, Any]:
        citations = list(payload.get("citations", []))
        support = str(payload.get("support_status", "unsupported"))
        conflict = str(payload.get("conflict_status", "none"))
        supported = support == "supported"
        conflicting = conflict == "conflict"
        if not supported:
            summary = "The retrieved evidence does not support a definitive answer."
        elif conflicting:
            summary = (
                "Multiple policies appear to apply; the conflict must be resolved "
                "before drafting."
            )
        else:
            summary = "The official policy evidence supports addressing the request."
        return {
            "summary": summary,
            "citations": citations,
            "unsupported_points": [] if supported else ["Insufficient policy support."],
            "conflict_warning": conflicting,
            "sufficient_for_drafting": bool(supported and not conflicting),
        }

    @staticmethod
    def _draft_response(payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("customer_name", "there"))
        allowed_actions = list(payload.get("allowed_actions", []))
        approval_required = bool(payload.get("approval_required", False))
        requires_more_info = bool(payload.get("requires_more_info", False))
        citations = list(payload.get("citations", []))
        missing = list(payload.get("missing_information", []))
        rule_outcome = str(payload.get("rule_outcome", ""))

        action = allowed_actions[0] if allowed_actions else "no_action"
        if requires_more_info:
            action = (
                "request_more_information"
                if "request_more_information" in allowed_actions
                else action
            )
            body = (
                f"Hello {name},\n\nThank you for getting in touch. To help "
                "further, could you please provide the missing details listed "
                "below? Once we have them we will review your request.\n\n"
                "Kind regards,\nMeridian & Co. Support"
            )
        elif approval_required:
            body = (
                f"Hello {name},\n\nThank you for contacting us. I have submitted "
                "your request for review by a supervisor and we will be in touch "
                "shortly with the outcome.\n\nKind regards,\nMeridian & Co. Support"
            )
        else:
            body = (
                f"Hello {name},\n\nThank you for contacting us. Based on our "
                "policy, here is the information relevant to your request.\n\n"
                "Kind regards,\nMeridian & Co. Support"
            )
        return {
            "subject": "Update on your request",
            "body": body,
            "citations": citations,
            "proposed_action": action,
            "approval_required": approval_required,
            "requires_human_review": approval_required,
            "unsupported_claims": [],
            "decision_summary": (
                rule_outcome
                or "Response drafted in line with the deterministic result."
            ),
            "missing_information": missing,
        }

    @staticmethod
    def _decision_summary(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "customer_intent": str(payload.get("customer_intent", "Unknown intent.")),
            "verified_facts": list(payload.get("verified_facts", [])),
            "policy_evidence": list(payload.get("policy_evidence", [])),
            "rule_outcome": str(
                payload.get("rule_outcome", "No deterministic result.")
            ),
            "next_step": str(payload.get("next_step", "No action.")),
            "approval_required": bool(payload.get("approval_required", False)),
            "uncertainties": list(payload.get("uncertainties", [])),
        }

    def _repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Re-synthesise the target task's output from its original payload."""
        target = payload.get("target_task")
        original = payload.get("original_payload", {})
        if isinstance(target, str) and isinstance(original, dict):
            try:
                return self._synthesise(ModelTaskType(target), original)
            except ValueError:  # pragma: no cover - defensive
                pass
        return dict(original) if isinstance(original, dict) else {}
