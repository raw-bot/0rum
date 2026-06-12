"""Non-regression backtest for proposed DSL mutations (validation step 4).

Replays a strategy over the last days of 1m candles with the simplest
possible fill model, aligned with loop.py rather than an idealised one
(Fincept pitfall #5):

- entry fill at the close of the signal candle;
- intrabar SL/TP on the following candles, conservative: the stop has
  priority when both could fill in the same candle;
- max_hold and the DSL exit group evaluated at candle close;
- same evaluator and the same 200-candle sliding window as live, so an
  evaluation error behaves identically in both contexts (pitfall #4).

The simulation never trades for real: it exists to REJECT degenerate
mutations (zero signals, simulated daily-loss breach, trade-count explosion)
before they reach strategy.yaml.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import httpx

from hermes_trading.dsl import CANDLE_BUFFER
from hermes_trading.dsl.evaluator import evaluate
from hermes_trading.paths import STATE_DIR

HISTORY_CACHE_PATH = STATE_DIR / "candle_history.json"
CACHE_MAX_AGE_MS = 2 * 3600 * 1000
KLINES_URL = "https://api.binance.com/api/v3/klines"
KLINES_PAGE = 1000


def _binance_symbol(asset: str) -> str:
    return asset.replace("/", "").upper()


def _fetch_history(asset: str, days: int) -> list[dict]:
    now_ms = int(time.time() * 1000)
    start = now_ms - days * 86_400_000
    candles: list[dict] = []
    with httpx.Client(timeout=15) as client:
        cursor = start
        while cursor < now_ms:
            response = client.get(
                KLINES_URL,
                params={
                    "symbol": _binance_symbol(asset),
                    "interval": "1m",
                    "startTime": cursor,
                    "limit": KLINES_PAGE,
                },
            )
            response.raise_for_status()
            rows = response.json()
            if not rows:
                break
            for row in rows:
                candles.append(
                    {
                        "ts": int(row[0]),
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                    }
                )
            cursor = int(rows[-1][0]) + 60_000
    return candles


def load_history(asset: str, days: int = 7) -> list[dict]:
    """7 days of 1m candles, via a local cache to spare the public API."""
    needed = days * 1440
    if HISTORY_CACHE_PATH.exists():
        try:
            cached = json.loads(HISTORY_CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            cached = {}
        candles = cached.get("candles", [])
        if (
            cached.get("asset") == asset
            and len(candles) >= needed * 0.95
            and candles
            and int(time.time() * 1000) - int(candles[-1]["ts"]) <= CACHE_MAX_AGE_MS
        ):
            return candles[-needed:]
    candles = _fetch_history(asset, days)
    if len(candles) < needed * 0.5:
        raise RuntimeError(f"history fetch returned {len(candles)} candles, expected ~{needed}")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_CACHE_PATH.write_text(json.dumps({"asset": asset, "candles": candles}))
    return candles[-needed:]


def _day_of(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=UTC).date().isoformat()
    except (TypeError, ValueError, OSError):
        return "unknown"


def simulate(groups: dict, risk: dict, goal: dict, candles: list[dict], *, buffer: int = CANDLE_BUFFER) -> dict:
    """Replay one strategy; returns counts, trades, and per-day account returns."""
    stop_pct = float(risk.get("stop_loss_pct", 2.0)) / 100.0
    take_profit_pct = float(risk.get("take_profit_pct", 3.0)) / 100.0
    max_hold = int(risk.get("max_hold_candles", 30))
    position_size_pct = float(risk.get("position_size_r", 0.5)) / 100.0
    fee_rate = float(risk.get("fee_rate", 0.0004))
    starting_balance = float(goal.get("starting_balance_usd", 10000.0))
    balance = starting_balance

    trades: list[dict] = []
    daily_returns: dict[str, float] = {}
    errors: set[str] = set()
    entries_triggered = 0
    position: dict | None = None

    for index in range(buffer, len(candles) + 1):
        window = candles[index - buffer : index]
        candle = window[-1]
        close = candle["close"]

        if position is not None:
            exit_reason = None
            exit_price = close
            stop_price = position["entry_price"] * (1.0 - stop_pct)
            tp_price = position["entry_price"] * (1.0 + take_profit_pct)
            held = index - 1 - position["entry_index"]
            # Conservative intrabar: the stop wins when both levels are inside
            # the same candle's range.
            if candle["low"] <= stop_price:
                exit_reason, exit_price = "stop_loss", stop_price
            elif candle["high"] >= tp_price:
                exit_reason, exit_price = "take_profit", tp_price
            elif held >= max_hold:
                exit_reason = "max_hold"
            else:
                exit_eval = evaluate(groups["exit"], window)
                errors.update(exit_eval["errors"])
                if exit_eval["triggered"]:
                    exit_reason = "dsl_exit"
            if exit_reason:
                pnl_pct = (exit_price - position["entry_price"]) / position["entry_price"]
                notional = position["notional_usd"]
                net = pnl_pct * notional - notional * fee_rate * 2
                account_return = net / balance if balance > 0 else 0.0
                balance += net
                day = _day_of(candle["ts"])
                daily_returns[day] = daily_returns.get(day, 0.0) + account_return
                trades.append(
                    {
                        "entry_index": position["entry_index"],
                        "exit_index": index - 1,
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price,
                        "exit_reason": exit_reason,
                        "pnl_pct": pnl_pct,
                        "net_pnl_usd": net,
                        "account_return": account_return,
                    }
                )
                position = None
                continue  # same rule as live: no re-entry on the closing candle

        entry_eval = evaluate(groups["entry"], window)
        errors.update(entry_eval["errors"])
        if entry_eval["triggered"]:
            entries_triggered += 1
            if position is None:
                # Same sizing as loop._sizing: risk taken on the starting
                # balance, not the compounded one.
                risk_usd = starting_balance * position_size_pct
                notional = risk_usd / stop_pct if stop_pct else 0.0
                position = {"entry_index": index - 1, "entry_price": close, "notional_usd": notional}

    return {
        "trades": trades,
        "entries_triggered": entries_triggered,
        "daily_returns": daily_returns,
        "worst_day": min(daily_returns.values()) if daily_returns else 0.0,
        "errors": sorted(errors),
        "final_balance": balance,
    }
