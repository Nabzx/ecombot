"""Provider tests: mock determinism, scenario injection, fallback, safety."""

from __future__ import annotations

import json

import pytest
from app.llm.enums import ModelErrorCode, ModelTaskType, ProviderCapability
from app.llm.models import (
    ModelParameters,
    ModelProviderError,
    ModelRequest,
    ProviderCapabilities,
)
from app.llm.providers.hosted import HostedProvider
from app.llm.providers.mock import MockProvider
from app.llm.providers.ollama import OllamaProvider
from app.llm.router import ProviderRouter


def _request(task: ModelTaskType, payload: dict, scenario: str = "") -> ModelRequest:
    meta = {"mock_payload": json.dumps(payload)}
    if scenario:
        meta["mock_scenario"] = scenario
    return ModelRequest(
        task_type=task,
        model="mock-deterministic-v1",
        system_message="sys",
        user_message="usr",
        parameters=ModelParameters(),
        correlation_id="corr-1",
        trace_metadata=meta,
    )


async def test_mock_is_deterministic() -> None:
    mock = MockProvider()
    req = _request(ModelTaskType.TICKET_CLASSIFICATION, {"customer_text": "refund"})
    first = await mock.generate(req)
    second = await mock.generate(req)
    assert first.raw_text == second.raw_text


async def test_mock_covers_every_task() -> None:
    mock = MockProvider()
    for task in ModelTaskType:
        if task == ModelTaskType.STRUCTURED_OUTPUT_REPAIR:
            payload = {"target_task": "ticket_classification", "original_payload": {}}
        else:
            payload = {
                "customer_text": "refund",
                "citations": [],
                "allowed_actions": [],
            }
        response = await mock.generate(_request(task, payload))
        assert response.success


@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        ("timeout", ModelErrorCode.PROVIDER_TIMEOUT),
        ("unavailable", ModelErrorCode.PROVIDER_UNAVAILABLE),
        ("rate_limited", ModelErrorCode.PROVIDER_RATE_LIMITED),
        ("auth_failed", ModelErrorCode.PROVIDER_AUTHENTICATION_FAILED),
    ],
)
async def test_mock_injects_failures(scenario: str, code: ModelErrorCode) -> None:
    mock = MockProvider()
    with pytest.raises(ModelProviderError) as exc:
        await mock.generate(_request(ModelTaskType.TICKET_CLASSIFICATION, {}, scenario))
    assert exc.value.code == code


async def test_mock_malformed_output_is_not_parseable() -> None:
    mock = MockProvider()
    response = await mock.generate(
        _request(
            ModelTaskType.TICKET_CLASSIFICATION,
            {"customer_text": "refund"},
            "malformed_json",
        )
    )
    assert response.parsed_output is None


def test_mock_capabilities() -> None:
    caps = MockProvider().capabilities
    assert caps.has(ProviderCapability.NATIVE_STRUCTURED_OUTPUTS)
    assert caps.has(ProviderCapability.TOKEN_USAGE)


async def test_ollama_disabled_fails_clearly() -> None:
    provider = OllamaProvider(
        base_url="http://localhost:11434", model="x", enabled=False
    )
    assert not provider.is_available()
    with pytest.raises(ModelProviderError) as exc:
        await provider.generate(_request(ModelTaskType.TICKET_CLASSIFICATION, {}))
    assert exc.value.code == ModelErrorCode.INVALID_PROVIDER_CONFIGURATION


async def test_hosted_missing_key_fails_clearly() -> None:
    provider = HostedProvider(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key=None,
        enabled=True,
        price_table_version="price-table-2026-07",
    )
    assert not provider.is_available()
    with pytest.raises(ModelProviderError) as exc:
        await provider.generate(_request(ModelTaskType.TICKET_CLASSIFICATION, {}))
    assert exc.value.code == ModelErrorCode.PROVIDER_AUTHENTICATION_FAILED


class _Failing:
    provider_name = "hosted"
    default_model = "x"
    capabilities = ProviderCapabilities()

    def __init__(self, code: ModelErrorCode) -> None:
        self._code = code

    def is_available(self) -> bool:
        return True

    async def generate(self, request: ModelRequest):  # type: ignore[no-untyped-def]
        raise ModelProviderError(self._code, "boom", provider="hosted")


async def test_router_records_fallback_for_retryable_failure() -> None:
    router = ProviderRouter(
        {
            "hosted": _Failing(ModelErrorCode.PROVIDER_UNAVAILABLE),
            "mock": MockProvider(),
        },
        fallback_order=["hosted", "mock"],
        max_attempts=2,
        backoff_base=0,
    )
    result = await router.route(
        _request(ModelTaskType.TICKET_CLASSIFICATION, {"customer_text": "refund"}),
        requested_provider="hosted",
        fallback_allowed=True,
    )
    assert result.actual_provider == "mock"
    assert result.fallback_from == "hosted"
    assert result.attempts >= 3


async def test_router_does_not_fall_back_on_auth_failure() -> None:
    router = ProviderRouter(
        {
            "hosted": _Failing(ModelErrorCode.PROVIDER_AUTHENTICATION_FAILED),
            "mock": MockProvider(),
        },
        fallback_order=["hosted", "mock"],
        max_attempts=2,
        backoff_base=0,
    )
    with pytest.raises(ModelProviderError) as exc:
        await router.route(
            _request(ModelTaskType.TICKET_CLASSIFICATION, {"customer_text": "x"}),
            requested_provider="hosted",
            fallback_allowed=True,
        )
    assert exc.value.code == ModelErrorCode.PROVIDER_AUTHENTICATION_FAILED
