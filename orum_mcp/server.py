"""orum-mcp — read-only MCP server over stdio for Claude Code and Codex.

Implements the MCP subset both clients need (initialize, tools/list,
tools/call, ping) as newline-delimited JSON-RPC 2.0 on stdin/stdout, in pure
stdlib — no new dependency, no network socket, local stdio only. Diagnostics
go to stderr; stdout carries nothing but JSON-RPC frames.

Launch:  python -m orum_mcp.server   (from the repo root, e.g. via `uv run`)
Stop:    close stdin or send SIGINT — 0rum itself is unaffected either way.
"""

from __future__ import annotations

import json
import sys
import threading

from orum_mcp import __version__
from orum_mcp.redact import bounded_json, redact
from orum_mcp.tools import ToolError, call_tool, tool_definitions

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
TOOL_TIMEOUT_SECONDS = 20.0

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


def _log(message: str) -> None:
    print(f"[orum-mcp] {message}", file=sys.stderr, flush=True)


def _run_with_timeout(func, timeout: float):
    outcome: dict = {}

    def target() -> None:
        try:
            outcome["value"] = func()
        except BaseException as exc:  # propagated to the caller below
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"tool exceeded {timeout:.0f}s timeout")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def _initialize_result(params: dict) -> dict:
    requested = str(params.get("protocolVersion", ""))
    version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "orum-mcp", "version": __version__},
        "instructions": (
            "Read-only observability over the 0rum paper-trading bot. No tool can "
            "trade, mutate state, reach the network or reveal secrets."
        ),
    }


def _call_tool_result(params: dict) -> dict:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    try:
        payload = _run_with_timeout(lambda: call_tool(name, arguments), TOOL_TIMEOUT_SECONDS)
        text = bounded_json(redact(payload))
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except ToolError as exc:
        detail = {"error": "invalid_request", "message": str(exc)}
    except TimeoutError as exc:
        detail = {"error": "timeout", "message": str(exc)}
    except Exception as exc:  # a broken state file must yield a structured error
        detail = {"error": type(exc).__name__, "message": str(exc)}
    return {
        "content": [{"type": "text", "text": json.dumps(detail, ensure_ascii=False)}],
        "isError": True,
    }


def handle_message(message: dict) -> dict | None:
    """Process one JSON-RPC message; return the response dict or None (notification)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        if isinstance(message, dict) and "id" in message:
            return _error(message.get("id"), JSONRPC_INVALID_REQUEST, "invalid JSON-RPC request")
        return None
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}
    is_notification = "id" not in message

    if method == "initialize":
        return _result(msg_id, _initialize_result(params))
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": tool_definitions()})
    if method == "tools/call":
        return _result(msg_id, _call_tool_result(params))
    if is_notification:
        return None
    return _error(msg_id, JSONRPC_METHOD_NOT_FOUND, f"method not found: {method}")


def _result(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def main() -> None:
    _log(f"orum-mcp {__version__} ready (stdio, read-only)")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = _error(None, JSONRPC_PARSE_ERROR, "parse error")
        else:
            try:
                response = handle_message(message)
            except Exception as exc:  # the server loop must never die on one message
                _log(f"internal error: {exc!r}")
                response = _error(
                    message.get("id") if isinstance(message, dict) else None,
                    JSONRPC_INTERNAL_ERROR,
                    "internal error",
                )
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    _log("stdin closed, exiting")


if __name__ == "__main__":
    main()
