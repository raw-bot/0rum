"""Read-only backtests: reuse orum.dsl.backtest.simulate() on the LOCAL candle
cache only.

orum.dsl.backtest.load_history() can fetch Binance when its cache is stale;
this adapter deliberately never calls it. When the cache is missing or does
not match, it returns a structured error instead of touching the network —
the MCP server must never place a request anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

from orum import dashboard as dash
from orum.dsl import CANDLE_BUFFER
from orum.dsl.backtest import simulate

MAX_DAYS = 7
MAX_TRADES_RETURNED = 20


def _state_dir() -> Path:
    return Path(dash.STATE_DIR)


def _load_cached_candles(asset: str, days: int) -> tuple[list[dict] | None, str | None]:
    cache_path = _state_dir() / "candle_history.json"
    if not cache_path.exists():
        return None, (
            "candle_history.json missing — the offline replay uses the bot's local "
            "cache only (no network); run the bot's own backtest once to populate it"
        )
    try:
        data = json.loads(cache_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"candle_history.json unreadable: {type(exc).__name__}"
    if data.get("asset") != asset:
        return None, f"cache holds {data.get('asset')!r}, not {asset!r}"
    candles = (data.get("candles") or [])[-days * 1440 :]
    if len(candles) < CANDLE_BUFFER + 10:
        return None, f"cache too small: {len(candles)} candles (buffer is {CANDLE_BUFFER})"
    return candles, None


def _load_strategy(version: str) -> tuple[dict | None, str | None]:
    if version == "current":
        path = _state_dir() / "strategy.yaml"
    else:
        path = _state_dir() / "history" / f"{version}.yaml"
    if not path.exists():
        return None, f"strategy version {version!r} not found at state/{path.name}"
    doc = dash._read_yaml(path)
    if not doc.get("entry") or not doc.get("exit"):
        return None, f"strategy {version!r} has no DSL entry/exit groups (pre-DSL file?)"
    return doc, None


def _run(version: str, days: int) -> dict:
    goal = dash._read_yaml(_state_dir() / "goal.yaml")
    strategy, err = _load_strategy(version)
    if err:
        return {"ok": False, "error": err}
    asset = str(goal.get("asset", "BTC/USDT"))
    candles, err = _load_cached_candles(asset, days)
    if err:
        return {"ok": False, "error": err}
    result = simulate(
        {"entry": strategy["entry"], "exit": strategy["exit"]},
        strategy.get("risk", {}),
        goal,
        candles,
    )
    return {
        "ok": True,
        "version": version,
        "strategy_version_field": strategy.get("version"),
        "asset": asset,
        "days": days,
        "candles": {
            "count": len(candles),
            "first_ts": candles[0].get("ts"),
            "last_ts": candles[-1].get("ts"),
        },
        "trade_count": len(result["trades"]),
        "entries_triggered": result["entries_triggered"],
        "final_balance": result["final_balance"],
        "worst_day": result["worst_day"],
        "daily_returns": result["daily_returns"],
        "evaluation_errors": result["errors"],
        "last_trades": result["trades"][-MAX_TRADES_RETURNED:],
        "note": (
            "offline replay via orum.dsl.backtest.simulate on the local candle cache "
            "(no network, nothing written); same fill model as the validation backtest"
        ),
    }


def run_backtest(version: str = "current", days: int = 7) -> dict:
    return _run(version, min(int(days), MAX_DAYS))


def compare_runs(version_a: str, version_b: str, days: int = 7) -> dict:
    run_a = _run(version_a, min(int(days), MAX_DAYS))
    run_b = _run(version_b, min(int(days), MAX_DAYS))
    comparison = None
    if run_a.get("ok") and run_b.get("ok"):
        comparison = {
            key: {"a": run_a[key], "b": run_b[key], "delta": run_b[key] - run_a[key]}
            for key in ("trade_count", "entries_triggered", "final_balance", "worst_day")
        }
    for run in (run_a, run_b):
        run.pop("last_trades", None)
        run.pop("daily_returns", None)
    return {"run_a": run_a, "run_b": run_b, "comparison": comparison}
