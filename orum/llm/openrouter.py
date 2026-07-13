"""Strict JSON completions through OpenRouter, with no silent model fallback."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import jsonschema


OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(RuntimeError):
    """Base error for the OpenRouter boundary."""


class OpenRouterConfigError(OpenRouterError):
    """Raised before a request when provider configuration is incomplete."""


class OpenRouterResponseError(OpenRouterError):
    """Raised when the remote response cannot produce a validated object."""


@dataclass(frozen=True, slots=True)
class CompletionResult:
    payload: dict[str, Any]
    model: str
    latency_ms: float
    usage: dict[str, Any] | None
    request_id: str | None


class JsonCompletionClient(Protocol):
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> CompletionResult: ...


class OpenRouterClient:
    """Synchronous OpenRouter adapter suitable for injected/offline testing.

    Request shape follows OpenRouter's current strict structured-output guide:
    https://openrouter.ai/docs/guides/features/structured-outputs
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 1,
        http_client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise OpenRouterConfigError("OPENROUTER_API_KEY is required for a remote LLM call")
        if not isinstance(model, str) or not model.strip():
            raise OpenRouterConfigError("OpenRouter model must be non-empty")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise OpenRouterConfigError("OpenRouter timeout_seconds must be positive")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise OpenRouterConfigError("OpenRouter max_retries must be a non-negative integer")
        self._api_key = api_key.strip()
        self.model = model.strip()
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max_retries
        self._http_client = http_client or httpx.Client()
        self._sleeper = sleeper

    def __repr__(self) -> str:
        return (
            f"OpenRouterClient(model={self.model!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, max_retries={self.max_retries!r})"
        )

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> CompletionResult:
        if not isinstance(system, str) or not system.strip():
            raise OpenRouterConfigError("system prompt must be non-empty")
        if not isinstance(user, str) or not user.strip():
            raise OpenRouterConfigError("user prompt must be non-empty")
        if not isinstance(schema, dict):
            raise OpenRouterConfigError("schema must be an object")
        if not isinstance(schema_name, str) or not schema_name.strip():
            raise OpenRouterConfigError("schema_name must be non-empty")

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # OpenRouter defaults to provider failover. This lab disables it so
            # each journaled result has one pinned model/provider route.
            # https://openrouter.ai/docs/guides/routing/provider-selection
            "provider": {
                "allow_fallbacks": False,
                "require_parameters": True,
            },
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name.strip(),
                    "strict": True,
                    "schema": schema,
                },
            },
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        started = time.perf_counter()
        last_error: OpenRouterResponseError | None = None

        for attempt in range(self.max_retries + 1):
            response: httpx.Response | None = None
            retryable = False
            try:
                response = self._http_client.post(
                    OPENROUTER_CHAT_COMPLETIONS_URL,
                    headers=headers,
                    json=body,
                    timeout=self.timeout_seconds,
                )
            except httpx.TransportError as exc:
                last_error = OpenRouterResponseError(
                    f"OpenRouter transport failure: {type(exc).__name__}"
                )
                retryable = True
            else:
                if response.status_code >= 400:
                    message = self._safe_error_message(response)
                    last_error = OpenRouterResponseError(
                        f"OpenRouter HTTP {response.status_code}: {message}"
                    )
                    retryable = response.status_code == 429 or response.status_code >= 500
                else:
                    try:
                        return self._decode_result(
                            response=response,
                            schema=schema,
                            latency_ms=(time.perf_counter() - started) * 1000,
                        )
                    except OpenRouterResponseError as exc:
                        last_error = exc
                        retryable = not str(exc).startswith("OpenRouter model mismatch")

            if not retryable or attempt >= self.max_retries:
                assert last_error is not None
                raise last_error
            self._sleeper(self._retry_delay(response, attempt))

        raise OpenRouterResponseError("OpenRouter retry loop ended unexpectedly")

    def _decode_result(
        self,
        *,
        response: httpx.Response,
        schema: dict[str, Any],
        latency_ms: float,
    ) -> CompletionResult:
        try:
            envelope = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise OpenRouterResponseError("OpenRouter returned a non-JSON envelope") from exc
        if not isinstance(envelope, Mapping):
            raise OpenRouterResponseError("OpenRouter returned a non-object envelope")
        if envelope.get("error"):
            raise OpenRouterResponseError("OpenRouter returned an in-band provider error")

        actual_model = envelope.get("model")
        if actual_model != self.model:
            raise OpenRouterResponseError(
                f"OpenRouter model mismatch: requested {self.model!r}, received {actual_model!r}"
            )
        try:
            content = envelope["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterResponseError("OpenRouter response has no assistant content") from exc
        if not isinstance(content, str) or not content.strip():
            raise OpenRouterResponseError("OpenRouter returned empty assistant content")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OpenRouterResponseError("OpenRouter assistant content is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise OpenRouterResponseError("OpenRouter assistant content must be a JSON object")
        try:
            jsonschema.validate(instance=payload, schema=schema)
        except jsonschema.ValidationError as exc:
            raise OpenRouterResponseError(
                f"OpenRouter assistant content failed schema validation: {exc.message}"
            ) from exc

        usage = envelope.get("usage")
        normalized_usage = dict(usage) if isinstance(usage, Mapping) else None
        request_id = envelope.get("id")
        return CompletionResult(
            payload=payload,
            model=actual_model,
            latency_ms=latency_ms,
            usage=normalized_usage,
            request_id=request_id if isinstance(request_id, str) else None,
        )

    def _safe_error_message(self, response: httpx.Response) -> str:
        message = "request failed"
        try:
            envelope = response.json()
            error = envelope.get("error") if isinstance(envelope, Mapping) else None
            if isinstance(error, Mapping) and isinstance(error.get("message"), str):
                message = error["message"]
        except (json.JSONDecodeError, ValueError):
            pass
        return message.replace(self._api_key, "[redacted]")[:500]

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        if response is not None:
            value = response.headers.get("Retry-After")
            if value is not None:
                try:
                    delay = float(value)
                except ValueError:
                    pass
                else:
                    if 0 <= delay <= 60:
                        return delay
        return min(0.25 * (2**attempt), 5.0)
