from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime

import aiofiles
import yaml

from hermes_trading.adapters import macro, news, onchain, price
from hermes_trading.adapters.base import require_schema
from hermes_trading.market_regime import rolling_return_regime
from hermes_trading.paths import HEARTBEAT_PATH, STATE_DIR, STRATEGY_PATH, TRADES_PATH

POSITION_PATH = STATE_DIR / "open_position.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for before, after in zip(closes[-period - 1 : -1], closes[-period:]):
        delta = after - before
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


async def _retry(name: str, call, *, attempts: int = 3) -> dict:
    delay = 1.0
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return require_schema(await call())
        except Exception as exc:  # noqa: BLE001 - the loop records adapter failures uniformly.
            last_error = exc
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError(f"{name} adapter failed after {attempts} attempts: {last_error}") from last_error


async def _append_jsonl(path, payload: dict) -> None:
    async with aiofiles.open(path, "a") as handle:
        await handle.write(json.dumps(payload, sort_keys=True) + "\n")


async def _write_heartbeat(payload: dict) -> None:
    async with aiofiles.open(HEARTBEAT_PATH, "w") as handle:
        await handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")


async def _write_json(path, payload: dict) -> None:
    async with aiofiles.open(path, "w") as handle:
        await handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _load_recent_trades(limit: int = 50) -> list[dict]:
    if not TRADES_PATH.exists():
        return []
    lines = [line for line in TRADES_PATH.read_text().splitlines() if line.strip()]
    return [json.loads(line) for line in lines[-limit:]]


def signal_id(asset: str, strategy: dict, market: dict) -> str:
    entry = strategy.get("entry", {})
    candle_ts = market.get("last_candle_ts", "unknown")
    return "|".join(
        [
            asset,
            str(strategy.get("version", "01")),
            str(entry.get("indicator", "rsi")),
            str(entry.get("direction", "long")),
            str(entry.get("threshold", 30)),
            str(candle_ts),
        ]
    )


def should_record_signal(current_signal_id: str, recent_trades: list[dict]) -> bool:
    return all(trade.get("signal_id") != current_signal_id for trade in recent_trades)


def _load_open_position() -> dict | None:
    if not POSITION_PATH.exists():
        return None
    payload = json.loads(POSITION_PATH.read_text() or "{}")
    return payload or None


def _sizing(strategy: dict, goal: dict, entry_price: float) -> dict:
    balance = float(goal.get("starting_balance_usd", 10000.0))
    risk_pct = float(strategy.get("position_size_r", 0.5)) / 100.0
    stop_pct = float(strategy.get("stop_loss_pct", 2.0)) / 100.0
    risk_usd = balance * risk_pct
    notional_usd = risk_usd / stop_pct if stop_pct else 0.0
    return {
        "risk_usd": risk_usd,
        "notional_usd": notional_usd,
        "qty_base": notional_usd / entry_price if entry_price else 0.0,
    }


def open_position_from_signal(asset: str, strategy: dict, goal: dict, market: dict, rsi: float, regime: dict | None = None) -> dict:
    entry_price = float(market["closes"][-1])
    sizing = _sizing(strategy, goal, entry_price)
    regime = regime or rolling_return_regime(market.get("closes", []))
    return {
        "opened_at": _now(),
        "asset": asset,
        "signal_id": signal_id(asset, strategy, market),
        "opened_candle_ts": market.get("last_candle_ts"),
        "opened_index": len(market.get("closes", [])) - 1,
        "strategy_version": strategy.get("version", "01"),
        "direction": strategy.get("entry", {}).get("direction", "long"),
        "entry_reason": f"rsi={rsi:.2f} <= threshold={float(strategy.get('entry', {}).get('threshold', 30)):.2f}",
        "entry_price": entry_price,
        "rsi_at_entry": rsi,
        "market_regime_at_entry": regime.get("label", "unknown"),
        "market_regime_reason_at_entry": regime.get("reason", "not classified"),
        "price_source_at_entry": market.get("source", "unknown"),
        **sizing,
        "mode": os.getenv("HERMES_TRADING_MODE", "paper"),
    }


def _held_candles(position: dict, market: dict) -> int:
    try:
        candle_delta = int(market.get("last_candle_ts", 0) or 0) - int(position.get("opened_candle_ts", 0) or 0)
        return max(0, round(candle_delta / 60000)) if candle_delta else 0
    except (TypeError, ValueError):
        opened_index = int(position.get("opened_index", len(market.get("closes", [])) - 1) or 0)
        current_index = len(market.get("closes", [])) - 1
        return max(0, current_index - opened_index)


def close_position_if_needed(position: dict, strategy: dict, market: dict, rsi: float, regime: dict | None = None) -> dict | None:
    current_price = float(market["closes"][-1])
    regime = regime or rolling_return_regime(market.get("closes", []))
    entry_price = float(position.get("entry_price", current_price))
    pnl_pct = (current_price - entry_price) / entry_price if entry_price else 0.0
    stop_pct = float(strategy.get("stop_loss_pct", 2.0)) / 100.0
    take_profit_pct = float(strategy.get("take_profit_pct", 3.0)) / 100.0
    max_hold = int(strategy.get("max_hold_candles", 30))
    exit_rsi = float(strategy.get("exit_rsi_threshold", 55))
    held_candles = _held_candles(position, market)

    exit_reason = None
    if pnl_pct <= -stop_pct:
        exit_reason = "stop_loss"
    elif pnl_pct >= take_profit_pct:
        exit_reason = "take_profit"
    elif rsi >= exit_rsi:
        exit_reason = "rsi_reversion"
    elif held_candles >= max_hold:
        exit_reason = "max_hold"

    if not exit_reason:
        return None

    notional_usd = float(position.get("notional_usd", 0.0))
    pnl_usd = pnl_pct * notional_usd
    fee_rate = float(strategy.get("fee_rate", 0.0004))
    fees_usd = notional_usd * fee_rate * 2
    return {
        "ts": _now(),
        "asset": position.get("asset"),
        "signal_id": position.get("signal_id"),
        "candle_ts": market.get("last_candle_ts"),
        "strategy_version": position.get("strategy_version", strategy.get("version", "01")),
        "direction": position.get("direction", "long"),
        "entry_reason": position.get("entry_reason"),
        "exit_reason": exit_reason,
        "rsi_at_entry": float(position.get("rsi_at_entry", 0.0)),
        "rsi_at_exit": rsi,
        "market_regime_at_entry": position.get("market_regime_at_entry", "unknown"),
        "market_regime_at_exit": regime.get("label", "unknown"),
        "market_regime_reason_at_exit": regime.get("reason", "not classified"),
        "price_source_at_entry": position.get("price_source_at_entry", "unknown"),
        "price_source_at_exit": market.get("source", "unknown"),
        "entry_price": entry_price,
        "exit_price": current_price,
        "held_candles": held_candles,
        "pnl_pct": pnl_pct,
        "risk_usd": float(position.get("risk_usd", 0.0)),
        "notional_usd": notional_usd,
        "qty_base": float(position.get("qty_base", 0.0)),
        "fees_usd": fees_usd,
        "pnl_usd": pnl_usd,
        "net_pnl_usd": pnl_usd - fees_usd,
        "mode": position.get("mode", os.getenv("HERMES_TRADING_MODE", "paper")),
    }


def market_decision(
    *,
    entry_fired: bool,
    position: dict | None,
    closed_trade: dict | None,
    rsi: float,
    threshold: float,
    current_signal_id: str,
    can_open: bool,
) -> dict:
    if closed_trade:
        return {
            "action": "close_position",
            "reason": f"Closed paper position via {closed_trade['exit_reason']} at pnl {closed_trade['pnl_pct'] * 100:.2f}%.",
            "signal_id": current_signal_id,
        }
    if can_open:
        return {
            "action": "open_position",
            "reason": f"RSI {rsi:.2f} is below entry threshold {threshold:.2f}.",
            "signal_id": current_signal_id,
        }
    if position:
        return {
            "action": "manage_position",
            "reason": "Position remains open; stop loss, take profit, RSI reversion, and max hold are not hit.",
            "signal_id": current_signal_id,
        }
    if entry_fired:
        return {
            "action": "wait_duplicate_signal",
            "reason": "Entry condition fired, but this candle signal was already handled.",
            "signal_id": current_signal_id,
        }
    return {
        "action": "wait",
        "reason": f"No long entry: RSI {rsi:.2f} is above threshold {threshold:.2f}.",
        "signal_id": current_signal_id,
    }


async def run_loop(goal: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    print("Booting hermes-trading worker", flush=True)
    consecutive_failures = 0
    interval = int(os.getenv("HERMES_LOOP_INTERVAL_SECONDS", "60"))

    while True:
        try:
            strategy = yaml.safe_load(STRATEGY_PATH.read_text()) or {}
            asset = goal.get("asset", "BTC/USDT")
            market, chain, headline, macro_data = await asyncio.gather(
                _retry("price", lambda: price.fetch(asset)),
                _retry("onchain", onchain.fetch),
                _retry("news", news.fetch),
                _retry("macro", macro.fetch),
            )

            closes = market["closes"]
            rsi = _rsi(closes)
            regime = rolling_return_regime(closes)
            threshold = float(strategy.get("entry", {}).get("threshold", 30))
            direction = strategy.get("entry", {}).get("direction", "long")
            entry_fired = direction == "long" and rsi <= threshold
            current_signal_id = signal_id(asset, strategy, market)
            position = _load_open_position()
            opened_position = False
            trade_closed = False
            closed_trade = close_position_if_needed(position, strategy, market, rsi, regime) if position else None

            if closed_trade:
                await _append_jsonl(TRADES_PATH, closed_trade)
                POSITION_PATH.unlink(missing_ok=True)
                position = None
                trade_closed = True
                print(f"closed paper trade {asset} pnl_pct={closed_trade['pnl_pct']:.5f}", flush=True)

            can_open = (
                entry_fired
                and position is None
                and not trade_closed
                and should_record_signal(current_signal_id, _load_recent_trades())
            )
            decision = market_decision(
                entry_fired=entry_fired,
                position=position,
                closed_trade=closed_trade,
                rsi=rsi,
                threshold=threshold,
                current_signal_id=current_signal_id,
                can_open=can_open,
            )
            if can_open:
                position = open_position_from_signal(asset, strategy, goal, market, rsi, regime)
                await _write_json(POSITION_PATH, position)
                opened_position = True
                print(f"opened paper position {asset} entry={position['entry_price']:.2f}", flush=True)

            await _write_heartbeat(
                {
                    "ts": _now(),
                    "asset": asset,
                    "last_price": closes[-1],
                    "rsi": rsi,
                    "market_regime": regime,
                    "entry_fired": entry_fired,
                    "position_open": position is not None,
                    "opened_position": opened_position,
                    "trade_recorded": trade_closed,
                    "decision_action": decision["action"],
                    "decision_reason": decision["reason"],
                    "signal_id": current_signal_id,
                    "price_source": market["source"],
                    "onchain_source": chain["source"],
                    "news_source": headline["source"],
                    "macro_source": macro_data["source"],
                }
            )
            consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001 - top-level worker loop must fail closed.
            consecutive_failures += 1
            print(f"worker failure {consecutive_failures}/5: {exc}", flush=True)
            if consecutive_failures >= 5:
                raise
        await asyncio.sleep(interval)
