"""Tool registry for orum-mcp: strictly read-only, validated arguments.

Every tool maps to a read-only adapter function. There is deliberately no
place_order / open_position / close_position / modify_position /
change_strategy / change_risk / change_config / enable_live_mode tool, no
free-form shell, no free-form SQL, and no network access.
"""

from __future__ import annotations

import re

import jsonschema

from orum_mcp.adapters import backtest_runner, state_reader

VERSION_PATTERN = r"^(current|v\d{4})$"
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 100}
_DAYS = {"type": "integer", "minimum": 1, "maximum": 7}
_VERSION = {"type": "string", "pattern": VERSION_PATTERN}


class ToolError(Exception):
    """Raised for unknown tools or invalid arguments."""


def _schema(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS: list[dict] = [
    {
        "name": "get_worker_status",
        "description": "Liveness of the 0rum engine: worker pid, paper-engine heartbeat freshness, watcher status.",
        "inputSchema": _schema(),
        "handler": lambda: state_reader.worker_status(),
    },
    {
        "name": "get_account_state",
        "description": "Unified paper account (balance, equity, open positions, recent fills) plus isolated LLM-lab accounts.",
        "inputSchema": _schema(),
        "handler": lambda: state_reader.account_state(),
    },
    {
        "name": "get_open_positions",
        "description": "Open paper positions (per strategy), legacy worker position and shadow open-position count.",
        "inputSchema": _schema(),
        "handler": lambda: state_reader.open_positions(),
    },
    {
        "name": "get_pending_orders",
        "description": "Pending orders. 0rum has no order book: returns the stop/TP brackets attached to open positions.",
        "inputSchema": _schema(),
        "handler": lambda: state_reader.pending_orders(),
    },
    {
        "name": "get_risk_state",
        "description": "Drawdown, guardrail status/action, drawdown thresholds and portfolio risk limits.",
        "inputSchema": _schema(),
        "handler": lambda: state_reader.risk_state(),
    },
    {
        "name": "get_current_signal_state",
        "description": "Last worker loop snapshot: DSL entry/exit evaluation, decision and reason, regime, price source.",
        "inputSchema": _schema(),
        "handler": lambda: state_reader.current_signal_state(),
    },
    {
        "name": "get_last_decisions",
        "description": "Recent decisions: worker's last action, reflection (hypotheses) outcomes, LLM-lab decisions.",
        "inputSchema": _schema({"limit": _LIMIT}),
        "handler": lambda limit=10: state_reader.last_decisions(limit=limit),
    },
    {
        "name": "explain_rejected_signal",
        "description": "Verbatim rejection/quarantine records from the journals, optionally filtered by a substring (e.g. a decision_id). Never invents a reason.",
        "inputSchema": _schema({"query": {"type": "string", "maxLength": 200}, "limit": _LIMIT}),
        "handler": lambda query=None, limit=5: state_reader.explain_rejected_signal(query=query, limit=limit),
    },
    {
        "name": "explain_trade",
        "description": "One closed paper trade with its raw open/close fills and the events logged while it was open. Defaults to the most recent trade.",
        "inputSchema": _schema({
            "strategy_id": {"type": "string", "maxLength": 100},
            "ts_prefix": {"type": "string", "maxLength": 35},
        }),
        "handler": lambda strategy_id=None, ts_prefix=None: state_reader.explain_trade(
            strategy_id=strategy_id, ts_prefix=ts_prefix
        ),
    },
    {
        "name": "get_recent_trades",
        "description": "Closed trades, newest first, from the unified paper ledger (source=paper) or the retired mono-asset ledger (source=legacy).",
        "inputSchema": _schema({
            "limit": _LIMIT,
            "strategy_id": {"type": "string", "maxLength": 100},
            "source": {"type": "string", "enum": ["paper", "legacy"]},
        }),
        "handler": lambda limit=20, strategy_id=None, source="paper": state_reader.recent_trades(
            limit=limit, strategy_id=strategy_id, source=source
        ),
    },
    {
        "name": "get_strategy_metrics",
        "description": "Account-level fee-inclusive metrics (win rate, drawdown, official score) plus per-strategy breakdown and configured strategies.",
        "inputSchema": _schema(),
        "handler": lambda: state_reader.strategy_metrics(),
    },
    {
        "name": "get_shadow_portfolios",
        "description": "Research shadow portfolios (Kelly engines), AK MACD shadow tail and forecast gate state.",
        "inputSchema": _schema({"limit": _LIMIT}),
        "handler": lambda limit=10: state_reader.shadow_portfolios(limit=limit),
    },
    {
        "name": "get_recent_errors",
        "description": "Incident events (failures, guardrail transitions, quarantines) and current DSL evaluation errors; optional bounded raw .err log tails.",
        "inputSchema": _schema({"limit": _LIMIT, "include_raw_logs": {"type": "boolean"}}),
        "handler": lambda limit=20, include_raw_logs=False: state_reader.recent_errors(
            limit=limit, include_raw_logs=include_raw_logs
        ),
    },
    {
        "name": "get_runtime_config",
        "description": "Actually-loaded configuration: goal.yaml, current strategy DSL, portfolio config (+runtime override), strategy history versions, ORUM_*/0RUM_* env. Secrets masked.",
        "inputSchema": _schema(),
        "handler": lambda: state_reader.runtime_config(),
    },
    {
        "name": "run_existing_backtest",
        "description": "Offline replay of a strategy version ('current' or 'vNNNN') with 0rum's existing simulate() on the local candle cache. Read-only, no network, nothing written.",
        "inputSchema": _schema({"version": _VERSION, "days": _DAYS}),
        "handler": lambda version="current", days=7: backtest_runner.run_backtest(version=version, days=days),
    },
    {
        "name": "compare_backtest_runs",
        "description": "Run the existing offline backtest for two strategy versions on the same cached candles and diff the metrics.",
        "inputSchema": _schema(
            {"version_a": _VERSION, "version_b": _VERSION, "days": _DAYS},
            required=["version_a", "version_b"],
        ),
        "handler": lambda version_a, version_b, days=7: backtest_runner.compare_runs(
            version_a=version_a, version_b=version_b, days=days
        ),
    },
]

_INDEX = {tool["name"]: tool for tool in TOOLS}

# Explicit guarantee, also asserted by tests: no mutating tool exists.
FORBIDDEN_TOOL_NAMES = frozenset({
    "place_order", "open_position", "close_position", "modify_position",
    "change_strategy", "change_risk", "change_config", "enable_live_mode",
})
assert not FORBIDDEN_TOOL_NAMES & set(_INDEX), "trading tools must never exist"


def tool_definitions() -> list[dict]:
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in TOOLS
    ]


def call_tool(name: str, arguments: dict | None):
    tool = _INDEX.get(name)
    if tool is None:
        raise ToolError(f"unknown tool: {name!r}")
    arguments = arguments or {}
    if not isinstance(arguments, dict):
        raise ToolError("arguments must be an object")
    try:
        jsonschema.validate(arguments, tool["inputSchema"])
    except jsonschema.ValidationError as exc:
        raise ToolError(f"invalid arguments for {name}: {exc.message}") from exc
    if name in ("run_existing_backtest", "compare_backtest_runs"):
        for key in ("version", "version_a", "version_b"):
            value = arguments.get(key)
            if value is not None and not re.fullmatch(VERSION_PATTERN, value):
                raise ToolError(f"invalid {key}: {value!r}")
    return tool["handler"](**arguments)
