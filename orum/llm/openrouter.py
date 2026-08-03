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
NVIDIA_CHAT_COMPLETIONS_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


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

    provider_label = "OpenRouter"
    credential_name = "OPENROUTER_API_KEY"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 1,
        max_completion_tokens: int = 4096,
        http_client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise OpenRouterConfigError(
                f"{self.credential_name} is required for a remote LLM call"
            )
        if not isinstance(model, str) or not model.strip():
            raise OpenRouterConfigError(f"{self.provider_label} model must be non-empty")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise OpenRouterConfigError(f"{self.provider_label} timeout_seconds must be positive")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise OpenRouterConfigError(
                f"{self.provider_label} max_retries must be a non-negative integer"
            )
        if (
            isinstance(max_completion_tokens, bool)
            or not isinstance(max_completion_tokens, int)
            or max_completion_tokens <= 0
        ):
            raise OpenRouterConfigError(
                f"{self.provider_label} max_completion_tokens must be a positive integer"
            )
        self._api_key = api_key.strip()
        self.model = model.strip()
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max_retries
        self.max_completion_tokens = max_completion_tokens
        self._http_client = http_client or httpx.Client()
        self._sleeper = sleeper

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(model={self.model!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, max_retries={self.max_retries!r}, "
            f"max_completion_tokens={self.max_completion_tokens!r})"
        )

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> CompletionResult:
        self._validate_request(
            system=system, user=user, schema=schema, schema_name=schema_name
        )

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
            # The current Nemotron route only advertises the OpenAI-compatible
            # key.  `max_completion_tokens` is rejected when strict provider
            # parameter matching is enabled, whereas `max_tokens` is accepted.
            "max_tokens": self.max_completion_tokens,
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
                    f"{self.provider_label} transport failure: {type(exc).__name__}"
                )
                retryable = True
            else:
                if response.status_code >= 400:
                    message = self._safe_error_message(response)
                    last_error = OpenRouterResponseError(
                        f"{self.provider_label} HTTP {response.status_code}: {message}"
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
                        retryable = not str(exc).startswith(
                            f"{self.provider_label} model mismatch"
                        )

            if not retryable or attempt >= self.max_retries:
                assert last_error is not None
                raise last_error
            self._sleeper(self._retry_delay(response, attempt))

        raise OpenRouterResponseError(f"{self.provider_label} retry loop ended unexpectedly")

    @staticmethod
    def _validate_request(
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> None:
        if not isinstance(system, str) or not system.strip():
            raise OpenRouterConfigError("system prompt must be non-empty")
        if not isinstance(user, str) or not user.strip():
            raise OpenRouterConfigError("user prompt must be non-empty")
        if not isinstance(schema, dict):
            raise OpenRouterConfigError("schema must be an object")
        if not isinstance(schema_name, str) or not schema_name.strip():
            raise OpenRouterConfigError("schema_name must be non-empty")

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
            raise OpenRouterResponseError(
                f"{self.provider_label} returned a non-JSON envelope"
            ) from exc
        if not isinstance(envelope, Mapping):
            raise OpenRouterResponseError(f"{self.provider_label} returned a non-object envelope")
        if envelope.get("error"):
            raise OpenRouterResponseError(
                f"{self.provider_label} returned an in-band provider error"
            )

        actual_model = envelope.get("model")
        if actual_model != self.model:
            raise OpenRouterResponseError(
                f"{self.provider_label} model mismatch: requested {self.model!r}, "
                f"received {actual_model!r}"
            )
        try:
            content = envelope["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterResponseError(
                f"{self.provider_label} response has no assistant content"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise OpenRouterResponseError(
                f"{self.provider_label} returned empty assistant content"
            )
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OpenRouterResponseError(
                f"{self.provider_label} assistant content is not valid JSON"
            ) from exc
        # Nemotron occasionally wraps its otherwise structured response in a
        # one-item JSON array.  Unwrap only that unambiguous transport quirk;
        # the exact object is still validated against the requested schema.
        if isinstance(payload, list) and len(payload) == 1:
            payload = payload[0]
        if not isinstance(payload, dict):
            raise OpenRouterResponseError(
                f"{self.provider_label} assistant content must be a JSON object"
            )
        try:
            jsonschema.validate(instance=payload, schema=schema)
        except jsonschema.ValidationError as exc:
            raise OpenRouterResponseError(
                f"{self.provider_label} assistant content failed schema validation: {exc.message}"
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


class NvidiaClient(OpenRouterClient):
    """Direct NVIDIA NIM adapter for the pinned Nemotron paper model.

    NVIDIA supports JSON mode, while the caller remains responsible for the
    schema validation performed by :class:`OpenRouterClient`.
    """

    provider_label = "NVIDIA"
    credential_name = "NVIDIA_API_KEY"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> CompletionResult:
        self._validate_request(
            system=system, user=user, schema=schema, schema_name=schema_name
        )

        structured_system = (
            f"{system}\n\n"
            "Réponds exclusivement par un seul objet JSON, sans Markdown ni texte "
            "hors JSON. Tous les champs requis du contrat suivant doivent être "
            "présents et respecter exactement leurs types et contraintes :\n"
            f"{json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
        )

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": structured_system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            # The dashboard/journals need a validated decision object, not the
            # model's private reasoning stream.  The Mephul interactive agent
            # can keep thinking enabled independently.
            "chat_template_kwargs": {"enable_thinking": False},
            "stream": False,
            "temperature": 0,
            "max_tokens": self.max_completion_tokens,
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
                    NVIDIA_CHAT_COMPLETIONS_URL,
                    headers=headers,
                    json=body,
                    timeout=self.timeout_seconds,
                )
            except httpx.TransportError as exc:
                last_error = OpenRouterResponseError(
                    f"{self.provider_label} transport failure: {type(exc).__name__}"
                )
                retryable = True
            else:
                if response.status_code >= 400:
                    message = self._safe_error_message(response)
                    last_error = OpenRouterResponseError(
                        f"{self.provider_label} HTTP {response.status_code}: {message}"
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
                        retryable = not str(exc).startswith(
                            f"{self.provider_label} model mismatch"
                        )

            if not retryable or attempt >= self.max_retries:
                assert last_error is not None
                raise last_error
            self._sleeper(self._retry_delay(response, attempt))

        raise OpenRouterResponseError(f"{self.provider_label} retry loop ended unexpectedly")
