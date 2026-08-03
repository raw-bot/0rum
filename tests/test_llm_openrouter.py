import json
import math

import httpx
import pytest

from orum.llm.openrouter import (
    NVIDIA_CHAT_COMPLETIONS_URL,
    NvidiaClient,
    OpenRouterClient,
    OpenRouterConfigError,
    OpenRouterResponseError,
)


# Current request/response contracts verified 2026-07-13:
# - endpoint and normalized response: https://openrouter.ai/docs/api/reference/overview
# - strict response_format shape: https://openrouter.ai/docs/guides/features/structured-outputs
# - Retry-After on 429/503: https://openrouter.ai/docs/api/reference/errors-and-debugging
# - usage fields/model capabilities: https://openrouter.ai/docs/guides/overview/models


def test_openrouter_pins_model_and_json_schema_without_logging_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "gen-1",
                "model": "deepseek/deepseek-v4-pro",
                "choices": [{"message": {"content": '{"bias":"neutral"}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
            },
        )

    client = OpenRouterClient(
        api_key="secret-value",
        model="deepseek/deepseek-v4-pro",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.complete_json(
        system="system",
        user="user",
        schema={
            "type": "object",
            "properties": {"bias": {"type": "string"}},
            "required": ["bias"],
            "additionalProperties": False,
        },
        schema_name="brief",
    )

    assert result.payload == {"bias": "neutral"}
    assert result.model == "deepseek/deepseek-v4-pro"
    assert result.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "total_tokens": 16,
    }
    assert "secret-value" not in repr(result)
    assert "secret-value" not in repr(client)
    assert seen["headers"]["authorization"] == "Bearer secret-value"
    assert seen["body"] == {
        "model": "deepseek/deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        "provider": {"allow_fallbacks": False, "require_parameters": True},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "brief",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"bias": {"type": "string"}},
                    "required": ["bias"],
                    "additionalProperties": False,
                },
            },
        },
        "stream": False,
        "max_tokens": 4096,
    }


def test_openrouter_unwraps_only_a_single_object_response_array():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "deepseek/deepseek-v4-pro",
                "choices": [{"message": {"content": '[{"bias":"neutral"}]'}}],
            },
        )

    client = OpenRouterClient(
        api_key="secret-value",
        model="deepseek/deepseek-v4-pro",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.complete_json(
        system="system",
        user="user",
        schema={
            "type": "object",
            "properties": {"bias": {"const": "neutral"}},
            "required": ["bias"],
            "additionalProperties": False,
        },
        schema_name="brief",
    )

    assert result.payload == {"bias": "neutral"}


def test_nvidia_uses_direct_endpoint_and_json_mode():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "nvidia-1",
                "model": "nvidia/nemotron-3-ultra-550b-a55b",
                "choices": [{"message": {"content": '{"bias":"neutral"}'}}],
            },
        )

    client = NvidiaClient(
        api_key="secret-value",
        model="nvidia/nemotron-3-ultra-550b-a55b",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.complete_json(
        system="system",
        user="user",
        schema={"type": "object", "properties": {"bias": {"type": "string"}}, "required": ["bias"]},
        schema_name="brief",
    )

    assert result.payload == {"bias": "neutral"}
    assert seen["url"] == NVIDIA_CHAT_COMPLETIONS_URL
    assert seen["body"] == {
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
        "messages": [
            {
                "role": "system",
                "content": (
                    "system\n\nRéponds exclusivement par un seul objet JSON, sans Markdown ni texte "
                    "hors JSON. Tous les champs requis du contrat suivant doivent être présents et "
                    "respecter exactement leurs types et contraintes :\n"
                    '{"properties":{"bias":{"type":"string"}},"required":["bias"],"type":"object"}'
                ),
            },
            {"role": "user", "content": "user"},
        ],
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
        "stream": False,
        "temperature": 0,
        "max_tokens": 4096,
    }


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_openrouter_rejects_missing_key_before_any_request(api_key):
    with pytest.raises(OpenRouterConfigError, match="OPENROUTER_API_KEY"):
        OpenRouterClient(api_key=api_key, model="deepseek/deepseek-v4-pro")


@pytest.mark.parametrize("timeout", [0, -1, math.nan, math.inf])
def test_openrouter_rejects_non_finite_or_non_positive_timeout(timeout):
    with pytest.raises(OpenRouterConfigError, match="timeout_seconds"):
        OpenRouterClient(
            api_key="secret-value",
            model="deepseek/deepseek-v4-pro",
            timeout_seconds=timeout,
        )


@pytest.mark.parametrize("max_completion_tokens", [0, -1, 1.5, True])
def test_openrouter_rejects_invalid_completion_token_cap(max_completion_tokens):
    with pytest.raises(OpenRouterConfigError, match="max_completion_tokens"):
        OpenRouterClient(
            api_key="secret-value",
            model="deepseek/deepseek-v4-pro",
            max_completion_tokens=max_completion_tokens,
        )


@pytest.mark.parametrize("content", ["", "not-json", "[]", '{"bias":"other"}'])
def test_openrouter_retries_invalid_or_schema_nonconforming_content_once(content):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "model": "deepseek/deepseek-v4-pro",
                "choices": [{"message": {"content": content}}],
            },
        )

    client = OpenRouterClient(
        api_key="secret-value",
        model="deepseek/deepseek-v4-pro",
        max_retries=1,
        sleeper=lambda _seconds: None,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(OpenRouterResponseError):
        client.complete_json(
            system="system",
            user="user",
            schema={
                "type": "object",
                "properties": {"bias": {"const": "neutral"}},
                "required": ["bias"],
            },
            schema_name="brief",
        )

    assert calls == 2


@pytest.mark.parametrize("status", [429, 500, 503])
def test_openrouter_retries_transient_status_once(status):
    calls = 0
    sleeps = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(status, headers={"Retry-After": "0.25"}, json={"error": {"message": "busy"}})
        return httpx.Response(
            200,
            json={
                "model": "deepseek/deepseek-v4-pro",
                "choices": [{"message": {"content": '{"ok":true}'}}],
            },
        )

    client = OpenRouterClient(
        api_key="secret-value",
        model="deepseek/deepseek-v4-pro",
        max_retries=1,
        sleeper=sleeps.append,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.complete_json(
        system="system",
        user="user",
        schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        schema_name="result",
    )

    assert result.payload == {"ok": True}
    assert calls == 2
    assert sleeps == [0.25]


def test_openrouter_does_not_retry_auth_or_bad_request_errors():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": {"message": "invalid credentials"}})

    client = OpenRouterClient(
        api_key="secret-value",
        model="deepseek/deepseek-v4-pro",
        max_retries=2,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(OpenRouterResponseError, match="HTTP 401"):
        client.complete_json(system="s", user="u", schema={"type": "object"}, schema_name="result")
    assert calls == 1


def test_openrouter_rejects_a_silently_changed_model():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "some/other-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
            },
        )

    client = OpenRouterClient(
        api_key="secret-value",
        model="deepseek/deepseek-v4-pro",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(OpenRouterResponseError, match="model mismatch"):
        client.complete_json(system="s", user="u", schema={"type": "object"}, schema_name="result")
