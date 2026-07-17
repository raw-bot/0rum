"""Recursive secret masking and response size bounding for every MCP reply.

Every tool result passes through redact() then bounded_json() before leaving
the server, so a secret-shaped key can never reach a client even if a state
file unexpectedly contains one.
"""

from __future__ import annotations

import json
import re

SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|apikey|secret|token|password|passwd|credential|bearer|"
    r"private[_-]?key|access[_-]?key)"
)
REDACTED = "***redacted***"
MAX_RESPONSE_BYTES = 200_000


def redact(value):
    """Return a copy of `value` with every secret-named field masked."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if isinstance(key, str) and SECRET_KEY_RE.search(key) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str) and SECRET_KEY_RE.search(value) and "=" in value:
        # e.g. a raw log line like "OPENROUTER_API_KEY=sk-..." — mask the value part.
        head = value.split("=", 1)[0]
        return f"{head}={REDACTED}"
    return value


def bounded_json(payload, max_bytes: int = MAX_RESPONSE_BYTES) -> str:
    """Serialize `payload`, replacing it with a structured error if oversized."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    return json.dumps(
        {
            "error": "response_too_large",
            "bytes": len(text.encode("utf-8")),
            "max_bytes": max_bytes,
            "hint": "reduce the limit/days parameter and retry",
        },
        ensure_ascii=False,
    )
