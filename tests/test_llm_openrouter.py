import json
import math

import httpx
import pytest

from orum.llm.openrouter import (
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
