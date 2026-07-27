"""Read-only adapters over 0rum's existing components.

Thin layer: every function reads state files and reuses the already-tested
helpers from orum.dashboard / orum.accounting / orum.score. No writes, no
network, no secrets. All state paths resolve through _state_dir() so tests
can redirect everything by patching orum.dashboard.STATE_DIR (the same
pattern the existing dashboard tests use).

Deliberately NOT used: orum.dashboard.build_snapshot() and the _markets /
_market_signals / _binance_* helpers — they call the Binance public API.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from orum import accounting
from orum import dashboard as dash
from orum.paths import PROJECT_ROOT
from orum.score import score

CONFIG_PORTFOLIO_PATH = PROJECT_ROOT / "config" / "portfolio.yaml"

# Event kinds that describe incidents rather than routine activity.
ERROR_KIND_TOKENS = (
    "fail", "error", "quarant", "freeze", "halt", "emergency", "reject", "outage",
)
REJECTION_TOKENS = ("reject", "quarantine", "refus")
RAW_LOG_FILES = ("paper.err", "engine.err", "dashboard.err", "llm_agent.err")
RAW_LOG_TAIL_BYTES = 16_384
RAW_LOG_TAIL_LINES = 20
# A worker heartbeat older than this is flagged stale: the loop runs every 60s,
# so 10 minutes of silence means the snapshot no longer describes "now".
HEARTBEAT_STALE_SECONDS = 600.0


def _state_dir() -> Path:
    return Path(dash.STATE_DIR)


def _goal() -> dict:
    return dash._read_yaml(_state_dir() / "goal.yaml")


def _strategy() -> dict:
    return dash._read_yaml(_state_dir() / "strategy.yaml")


def _heartbeat() -> dict:
    return dash._read_optional_json(_state_dir() / "heartbeat.json")


def _age_seconds(ts_str) -> float | None:
    parsed = dash._parse_ts(ts_str)
    if parsed is None:
        return None
    return (datetime.now(UTC) - parsed).total_seconds()


def _is_stale(age_seconds: float | None) -> bool:
    return age_seconds is None or age_seconds > HEARTBEAT_STALE_SECONDS


def worker_status() -> dict:
    heartbeat = _heartbeat()
    watcher = dash._read_optional_json(_state_dir() / "orum_watcher.json")
    pid = None
    try:
        pid = int((_state_dir() / "worker.pid").read_text().strip())
    except (OSError, ValueError):
        pass
    return {
        "engine_process": {
            "pid": pid,
            "running": bool(pid is not None and dash._pid_alive(pid)),
            "pid_file": "state/worker.pid",
            "note": "retired mono-asset worker (com.0rum.engine); superseded by com.0rum.paper on 2026-07-12, expected to stay not-running",
        },
        "paper_engine": dash._paper_worker(),
        "worker_heartbeat": {
            "present": bool(heartbeat),
            "ts": heartbeat.get("ts"),
            "age_seconds": _age_seconds(heartbeat.get("ts")),
            "stale": _is_stale(_age_seconds(heartbeat.get("ts"))),
            "guardrail": heartbeat.get("guardrail"),
            "price_source": heartbeat.get("price_source"),
            "note": "frozen snapshot from the retired mono-asset worker (last wrote 2026-07-19); it no longer runs, this will never update again — the unified paper engine above (paper_engine) is the live health signal",
        },
        "watcher": watcher or None,
    }


def account_state() -> dict:
    goal = _goal()
    paper = dash._paper_state(goal)
    paper["fills"] = paper.get("fills", [])[:10]
    paper["equity_curve"] = paper.get("equity_curve", [])[-50:]
    llm_accounts = {}
    for lane, name in (("reference", "llm_reference_account.json"), ("evolving", "llm_evolving_account.json")):
        data = dash._read_optional_json(_state_dir() / name)
        if data:
            llm_accounts[lane] = data
    return {
        "paper_account": paper,
        "llm_lab_accounts": llm_accounts,
        "legacy_worker_audit_7d": dash._legacy_audit(),
        "note": "paper_account (unified multi-strategy ledger) is the source of truth since 2026-07-08",
    }


def open_positions() -> dict:
    goal = _goal()
    paper = dash._paper_state(goal)
    legacy = dash._read_optional_json(_state_dir() / "open_position.json")
    shadow = dash._portfolio_shadow()
    return {
        "paper_positions": paper.get("open_positions", []),
        "open_count": paper.get("open_count", 0),
        "legacy_worker_position": legacy or None,
        "shadow_open_count": shadow.get("open_positions", 0),
    }


def pending_orders() -> dict:
    positions = dash._paper_state(_goal()).get("open_positions", [])
    brackets = [
        {
            "strategy_id": p.get("strategy_id"),
            "symbol": p.get("symbol"),
            "side": p.get("side"),
            "stop_loss_price": p.get("stop_loss_price"),
            "take_profit_price": p.get("take_profit_price"),
            "exit_policy": p.get("exit_policy"),
            "monitor_timeframe": p.get("monitor_timeframe"),
        }
        for p in positions
    ]
    return {
        "pending_orders": [],
        "supported": False,
        "note": (
            "0rum has no pending-order book: stops/take-profits are attributes of "
            "open positions (brackets below), evaluated by the paper engine each cycle"
        ),
        "position_brackets": brackets,
    }


def risk_state() -> dict:
    goal = _goal()
    trades = dash._paper_closed_trades()
    drawdown = accounting.max_drawdown(trades, goal)
    manual_resume = (_state_dir() / "manual_resume.ok").exists()
    try:
        from orum.loop import guardrail_action  # official pure decision function

        action = guardrail_action(drawdown, goal, manual_resume)
    except Exception as exc:  # never let a core import failure break observability
        action = f"unavailable ({type(exc).__name__})"
    portfolio_cfg = dash._read_yaml(CONFIG_PORTFOLIO_PATH)
    return {
        "drawdown": drawdown,
        "guardrail": dash._guardrail_status(drawdown, goal),
        "guardrail_action": action,
        "heartbeat_guardrail": _heartbeat().get("guardrail"),
        "manual_resume_ack": manual_resume,
        "thresholds": {
            key: goal.get(key)
            for key in (
                "soft_drawdown", "max_drawdown", "emergency_stop_drawdown",
                "daily_loss_limit", "risk_per_trade_min", "risk_per_trade_max",
            )
        },
        "portfolio_risk_limits": {
            key: portfolio_cfg.get(key)
            for key in ("max_total_stop_risk_pct", "max_symbol_stop_risk_pct")
        },
    }


def current_signal_state() -> dict:
    heartbeat = _heartbeat()
    if not heartbeat:
        return {"available": False, "note": "heartbeat.json absent — worker has not run in this state dir"}
    age = _age_seconds(heartbeat.get("ts"))
    return {
        "available": True,
        "ts": heartbeat.get("ts"),
        "age_seconds": age,
        "stale": _is_stale(age),
        "stale_warning": (
            "heartbeat is stale: this snapshot describes the retired mono-asset worker's "
            "LAST loop, not the present. Superseded by the unified paper portfolio "
            "(com.0rum.paper) on 2026-07-12; it will never update again — for the live "
            "multi-strategy signal state see state/paper.out or the paper_engine field "
            "on get_worker_status" if _is_stale(age) else None
        ),
        "asset": heartbeat.get("asset"),
        "decision_action": heartbeat.get("decision_action"),
        "decision_reason": heartbeat.get("decision_reason"),
        "dsl": heartbeat.get("dsl"),
        "signal_id": heartbeat.get("signal_id"),
        "price_source": heartbeat.get("price_source"),
        "market_regime": heartbeat.get("market_regime"),
        "rsi": heartbeat.get("rsi"),
        "last_price": heartbeat.get("last_price"),
        "note": "snapshot of the retired mono-asset worker's last loop iteration; frozen since 2026-07-19, not the current multi-strategy engine",
    }


def last_decisions(limit: int = 10) -> dict:
    heartbeat = _heartbeat()
    hypotheses = dash._read_jsonl_tail(_state_dir() / "hypotheses.jsonl", limit=limit)
    llm = dash._read_jsonl_tail(_state_dir() / "llm_decisions.jsonl", limit=limit)
    worker_last = None
    if heartbeat:
        worker_last = {
            "ts": heartbeat.get("ts"),
            "action": heartbeat.get("decision_action"),
            "reason": heartbeat.get("decision_reason"),
        }
    return {
        "worker_last_decision": worker_last,
        "reflection_decisions": hypotheses[::-1],
        "llm_lab_decisions": llm[::-1],
    }


def explain_rejected_signal(query: str | None = None, limit: int = 5) -> dict:
    matches: list[dict] = []
    sources = (
        ("llm_decisions", _state_dir() / "llm_decisions.jsonl"),
        ("hypotheses", _state_dir() / "hypotheses.jsonl"),
        ("events", _state_dir() / "events.jsonl"),
    )
    needle = str(query).lower() if query else None
    for source, path in sources:
        for record in dash._read_jsonl_tail(path, limit=500):
            blob = json.dumps(record, sort_keys=True, default=str).lower()
            if not any(token in blob for token in REJECTION_TOKENS):
                continue
            if needle and needle not in blob:
                continue
            matches.append({"source": source, "record": record})
    matches = matches[-limit:]
    return {
        "found": bool(matches),
        "matches": matches[::-1],
        "note": (
            "verbatim rejection records from the journals"
            if matches
            else "no matching rejection recorded in llm_decisions/hypotheses/events — "
                 "this server never invents a rejection reason"
        ),
    }


def explain_trade(strategy_id: str | None = None, ts_prefix: str | None = None) -> dict:
    trades = dash._paper_closed_trades()
    selected = [
        t for t in trades
        if (not strategy_id or t.get("strategy_id") == strategy_id)
        and (not ts_prefix or str(t.get("ts", "")).startswith(ts_prefix))
    ]
    if not selected:
        return {"found": False, "note": "no matching closed trade in the paper ledger"}
    trade = selected[-1]
    fills = [
        f for f in dash._read_jsonl(_state_dir() / "paper_fills.jsonl")
        if f.get("strategy_id") == trade.get("strategy_id")
        and f.get("ts") in (trade.get("ts"), trade.get("opened_at"))
    ]
    t_open, t_close = str(trade.get("opened_at", "")), str(trade.get("ts", ""))
    events = [
        e for e in dash._read_jsonl_tail(_state_dir() / "events.jsonl", limit=500)
        if t_open <= str(e.get("ts", "")) <= t_close
    ]
    return {
        "found": True,
        "trade": trade,
        "raw_fills": fills,
        "events_during_trade": events[:20],
        "note": "trade from the unified paper ledger; raw_fills are the verbatim open/close records",
    }


def recent_trades(limit: int = 20, strategy_id: str | None = None, source: str = "paper") -> dict:
    if source == "legacy":
        trades = dash._read_jsonl_tail(_state_dir() / "trades.jsonl", limit=limit * 3)
    else:
        trades = dash._paper_closed_trades()
    if strategy_id:
        trades = [t for t in trades if t.get("strategy_id") == strategy_id]
    return {
        "source": source,
        "trade_count_total": len(trades),
        "trades": trades[-limit:][::-1],
    }


def strategy_metrics() -> dict:
    goal = _goal()
    strategy = _strategy()
    trades = dash._paper_closed_trades()
    returns = accounting.account_returns(trades, goal)
    wins = [r for r in returns if r > 0]
    per_strategy: dict[str, dict] = {}
    for trade in trades:
        entry = per_strategy.setdefault(
            str(trade.get("strategy_id") or "unknown"),
            {"trades": 0, "wins": 0, "net_pnl_usd": 0.0},
        )
        entry["trades"] += 1
        entry["wins"] += 1 if float(trade.get("net_pnl_usd", 0.0) or 0.0) > 0 else 0
        entry["net_pnl_usd"] += float(trade.get("net_pnl_usd", 0.0) or 0.0)
    for entry in per_strategy.values():
        entry["win_rate"] = entry["wins"] / entry["trades"] if entry["trades"] else 0.0
    portfolio_cfg = dash._read_yaml(CONFIG_PORTFOLIO_PATH)
    return {
        "trade_count": len(trades),
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "avg_trade_return": (sum(returns) / len(returns)) if returns else 0.0,
        "best_trade_return": max(returns) if returns else 0.0,
        "worst_trade_return": min(returns) if returns else 0.0,
        "drawdown": accounting.max_drawdown(trades, goal),
        "compound_balance_usd": accounting.compound_balance(trades, goal),
        "score": score(trades, goal),
        "strategy_version": strategy.get("version"),
        "configured_strategies": [
            {k: s.get(k) for k in ("id", "engine", "symbol", "timeframe", "risk_pct", "entry_enabled")}
            for s in (portfolio_cfg.get("strategies") or [])
        ],
        "per_strategy": per_strategy,
    }


def _scalar_summary(value, list_preview: int = 0):
    """Keep scalars, summarize containers — forecast_gate.json embeds hundreds
    of KB of forecast history that would blow the response size cap."""
    if isinstance(value, dict):
        return {k: _scalar_summary(v) for k, v in value.items()}
    if isinstance(value, list):
        head = [_scalar_summary(v) for v in value[:list_preview]]
        return {"list_len": len(value), **({"head": head} if head else {})}
    return value


def shadow_portfolios(limit: int = 10) -> dict:
    shadow = dash._portfolio_shadow()
    shadow["equity_curve"] = shadow.get("equity_curve", [])[-50:]
    shadow["trades"] = shadow.get("trades", [])[:limit]
    ak_tail = dash._read_jsonl_tail(_state_dir() / "ak_macd_local_shadow.jsonl", limit=limit)
    forecast_gate = dash._read_optional_json(_state_dir() / "forecast_gate.json")
    return {
        "research_portfolio_shadow": shadow,
        "ak_macd_shadow_tail": ak_tail[::-1],
        "forecast_gate_summary": _scalar_summary(forecast_gate) if forecast_gate else None,
        "note": "forecast_gate is summarized (scalars + list lengths); the raw file can exceed the response cap",
    }


def _raw_log_tail(path: Path) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - RAW_LOG_TAIL_BYTES))
            chunk = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return [line[:500] for line in chunk.splitlines()[-RAW_LOG_TAIL_LINES:]]


def recent_errors(limit: int = 20, include_raw_logs: bool = False) -> dict:
    events = [
        e for e in dash._read_jsonl_tail(_state_dir() / "events.jsonl", limit=500)
        if any(token in str(e.get("kind", "")).lower() for token in ERROR_KIND_TOKENS)
    ]
    heartbeat = _heartbeat()
    out = {
        "incident_events": events[-limit:][::-1],
        "heartbeat_dsl_errors": (heartbeat.get("dsl") or {}).get("errors", []),
    }
    if include_raw_logs:
        raw = {}
        for name in RAW_LOG_FILES:
            path = _state_dir() / name
            if path.exists():
                raw[name] = {"format": "raw_text_tail", "lines": _raw_log_tail(path)}
        out["raw_log_tails"] = raw
    return out


def runtime_config() -> dict:
    history_dir = _state_dir() / "history"
    history = sorted(p.name for p in history_dir.glob("*.yaml")) if history_dir.exists() else []
    env = {
        key: value
        for key, value in os.environ.items()
        if key.startswith(("ORUM_", "0RUM_"))
    }
    return {
        "goal": _goal(),
        "strategy": _strategy(),
        "portfolio_config_static": dash._read_yaml(CONFIG_PORTFOLIO_PATH),
        "portfolio_config_runtime_override": dash._read_yaml(_state_dir() / "portfolio.yaml") or None,
        "strategy_history_versions": history,
        "environment": env,
        "note": "actually-loaded runtime config; secret-named values are masked, .env is never read",
    }
