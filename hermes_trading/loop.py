from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from datetime import UTC, datetime

import aiofiles
import yaml

from hermes_trading.accounting import compound_balance, max_drawdown
from hermes_trading.adapters import macro, news, onchain, price
from hermes_trading.adapters.base import require_schema
from hermes_trading.dsl import evaluator as dsl_evaluator
from hermes_trading.dsl import indicators as dsl_indicators
from hermes_trading.dsl.migrate import is_dsl_strategy, migrate_strategy_file, risk_value, strategy_dsl_groups
from hermes_trading.events import log_event
from hermes_trading.fsio import atomic_write_json, atomic_write_text
from hermes_trading.market_regime import rolling_return_regime
from hermes_trading.paths import HEARTBEAT_PATH, STATE_DIR, STRATEGY_PATH, TRADES_PATH

POSITION_PATH = STATE_DIR / "open_position.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def telemetry_rsi(candles: list[dict], period: int = 14) -> float | None:
    """Wilder RSI(14) kept as an observability metric (heartbeat, trade
    records); the trading decision itself comes from the DSL evaluator."""
    series = dsl_indicators.rsi(candles, period)
    if not series or math.isnan(series[-1]):
        return None
    return series[-1]


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
    atomic_write_json(HEARTBEAT_PATH, payload)


async def _write_json(path, payload: dict) -> None:
    atomic_write_json(path, payload)


def _load_recent_trades(limit: int | None = 50) -> list[dict]:
    if not TRADES_PATH.exists():
        return []
    lines = [line for line in TRADES_PATH.read_text().splitlines() if line.strip()]
    if limit is not None:
        lines = lines[-limit:]
    return [json.loads(line) for line in lines]


def price_is_offline(market: dict) -> bool:
    return market.get("source") == "offline_fallback"


def market_candles(market: dict) -> list[dict]:
    """OHLCV candles from the adapter; synthesized from closes when a payload
    predates the candles field (degenerate OHLC, close-only indicators stay exact)."""
    candles = market.get("candles")
    if candles:
        return candles
    return [
        {"ts": index, "open": float(close), "high": float(close), "low": float(close), "close": float(close), "volume": 0.0}
        for index, close in enumerate(market.get("closes", []))
    ]


def strategy_direction(strategy: dict) -> str:
    if "direction" in strategy:
        return str(strategy["direction"])
    return str(strategy.get("entry", {}).get("direction", "long"))


def entry_signal_fired(strategy: dict, candles: list[dict], market: dict) -> dict:
    """Evaluate the DSL entry group; returns the full EvalResult so errors and
    per-condition details reach the heartbeat. ["triggered"] drives trading."""
    if price_is_offline(market) or strategy_direction(strategy) != "long":
        return {"triggered": False, "details": [], "errors": []}
    groups = strategy_dsl_groups(strategy)
    return dsl_evaluator.evaluate(groups["entry"], candles)


def exit_signal_fired(strategy: dict, candles: list[dict]) -> dict:
    groups = strategy_dsl_groups(strategy)
    return dsl_evaluator.evaluate(groups["exit"], candles)


def signal_id(asset: str, strategy: dict, market: dict) -> str:
    """Hash of the canonical entry JSON + version; one signal per candle.

    Replaces the legacy indicator/direction/threshold concatenation, which
    could not represent a multi-condition entry."""
    entry = strategy_dsl_groups(strategy)["entry"]
    canonical = json.dumps(
        {"entry": entry, "version": strategy.get("version", "01")},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    candle_ts = market.get("last_candle_ts", "unknown")
    return f"{asset}|{digest}|{candle_ts}"


def should_record_signal(current_signal_id: str, recent_trades: list[dict]) -> bool:
    return all(trade.get("signal_id") != current_signal_id for trade in recent_trades)


def is_duplicate_close(closed_trade: dict | None, trades: list[dict]) -> bool:
    """True when this position's close was already recorded.

    A crash between appending the closed trade and unlinking
    open_position.json leaves the position behind; on restart it would be
    closed and counted a second time. A position's signal_id is unique, so a
    recorded trade with the same signal_id means the close already happened."""
    if not closed_trade:
        return False
    signal = closed_trade.get("signal_id")
    return signal is not None and any(trade.get("signal_id") == signal for trade in trades)


def position_is_stale(position: dict, *, now: datetime | None = None, max_age_hours: float | None = None) -> bool:
    """A position older than max_age_hours means the worker was down for a
    long stretch; closing it against the current price would record a trade
    spanning the whole outage and pollute every downstream metric."""
    if max_age_hours is None:
        max_age_hours = float(os.getenv("HERMES_MAX_POSITION_AGE_HOURS", "6"))
    now = now or datetime.now(UTC)
    try:
        opened_at = datetime.fromisoformat(str(position.get("opened_at")))
    except (TypeError, ValueError):
        return True
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=UTC)
    return (now - opened_at).total_seconds() > max_age_hours * 3600


def _quarantine_position(position: dict, reason: str) -> None:
    record = {**position, "quarantined_at": _now(), "quarantine_reason": reason}
    with (STATE_DIR / "position_quarantine.jsonl").open("a") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    POSITION_PATH.unlink(missing_ok=True)
    log_event("position_quarantined", reason, asset=position.get("asset"), signal_id=position.get("signal_id"))


def _load_open_position() -> dict | None:
    if not POSITION_PATH.exists():
        return None
    payload = json.loads(POSITION_PATH.read_text() or "{}")
    if not payload:
        return None
    if position_is_stale(payload):
        _quarantine_position(
            payload,
            f"opened_at={payload.get('opened_at')!r} exceeds HERMES_MAX_POSITION_AGE_HOURS; discarded instead of closing across the outage",
        )
        return None
    return payload


def _sizing(strategy: dict, goal: dict, entry_price: float) -> dict:
    balance = float(goal.get("starting_balance_usd", 10000.0))
    risk_pct = risk_value(strategy, "position_size_r", 0.5) / 100.0
    stop_pct = risk_value(strategy, "stop_loss_pct", 2.0) / 100.0
    risk_usd = balance * risk_pct
    notional_usd = risk_usd / stop_pct if stop_pct else 0.0
    return {
        "risk_usd": risk_usd,
        "notional_usd": notional_usd,
        "qty_base": notional_usd / entry_price if entry_price else 0.0,
    }


def open_position_from_signal(
    asset: str,
    strategy: dict,
    goal: dict,
    market: dict,
    rsi: float | None,
    regime: dict | None = None,
    entry_summary: str | None = None,
) -> dict:
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
        "direction": strategy_direction(strategy),
        "entry_reason": f"dsl: {entry_summary}" if entry_summary else "dsl entry group triggered",
        "entry_price": entry_price,
        "rsi_at_entry": rsi,
        "market_regime_at_entry": regime.get("label", "unknown"),
        "market_regime_reason_at_entry": regime.get("reason", "not classified"),
        "price_source_at_entry": market.get("source", "unknown"),
        **sizing,
        "mode": os.getenv("HERMES_TRADING_MODE", "paper"),
    }


def _held_candles(position: dict, market: dict) -> int:
    # Hold duration is counted in the STRATEGY's candles, not in minutes. Native
    # positions run on the 1m Binance feed (60000 ms); an external position
    # carries its own bar size in `candle_interval_ms` (e.g. 900000 for a 15m
    # TradingView strategy). Without this, a 15m position would count 15x too
    # many candles and hit max_hold ~15x too early. Missing/zero -> 60000.
    interval_ms = int(position.get("candle_interval_ms") or 0) or 60000
    try:
        candle_delta = int(market.get("last_candle_ts", 0) or 0) - int(position.get("opened_candle_ts", 0) or 0)
        return max(0, round(candle_delta / interval_ms)) if candle_delta else 0
    except (TypeError, ValueError):
        opened_index = int(position.get("opened_index", len(market.get("closes", [])) - 1) or 0)
        current_index = len(market.get("closes", [])) - 1
        return max(0, current_index - opened_index)


def close_position_if_needed(
    position: dict,
    strategy: dict,
    market: dict,
    rsi: float | None,
    regime: dict | None = None,
    exit_triggered: bool = False,
) -> dict | None:
    """Risk exits (stop, take profit, max hold) stay in code and are checked
    BEFORE the DSL exit group; the DSL only adds a signal-based exit."""
    current_price = float(market["closes"][-1])
    entry_price = float(position.get("entry_price", current_price))
    pnl_pct = (current_price - entry_price) / entry_price if entry_price else 0.0
    stop_pct = risk_value(strategy, "stop_loss_pct", 2.0) / 100.0
    take_profit_pct = risk_value(strategy, "take_profit_pct", 3.0) / 100.0
    max_hold = int(risk_value(strategy, "max_hold_candles", 30))
    held_candles = _held_candles(position, market)

    exit_reason = None
    if pnl_pct <= -stop_pct:
        exit_reason = "stop_loss"
    elif pnl_pct >= take_profit_pct:
        exit_reason = "take_profit"
    elif held_candles >= max_hold:
        exit_reason = "max_hold"
    elif exit_triggered:
        exit_reason = "dsl_exit"

    if not exit_reason:
        return None
    return _build_closed_trade(position, strategy, market, rsi, regime, exit_reason)


def _build_closed_trade(position: dict, strategy: dict, market: dict, rsi: float | None, regime: dict | None, exit_reason: str) -> dict:
    current_price = float(market["closes"][-1])
    regime = regime or rolling_return_regime(market.get("closes", []))
    entry_price = float(position.get("entry_price", current_price))
    pnl_pct = (current_price - entry_price) / entry_price if entry_price else 0.0
    held_candles = _held_candles(position, market)
    notional_usd = float(position.get("notional_usd", 0.0))
    pnl_usd = pnl_pct * notional_usd
    fee_rate = risk_value(strategy, "fee_rate", 0.0004)
    fees_usd = notional_usd * fee_rate * 2
    return {
        "ts": _now(),
        "asset": position.get("asset"),
        "signal_id": position.get("signal_id"),
        "opened_at": position.get("opened_at"),
        "opened_candle_ts": position.get("opened_candle_ts"),
        "candle_ts": market.get("last_candle_ts"),
        "strategy_version": position.get("strategy_version", strategy.get("version", "01")),
        "direction": position.get("direction", "long"),
        "entry_reason": position.get("entry_reason"),
        "exit_reason": exit_reason,
        "rsi_at_entry": position.get("rsi_at_entry"),
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


RESUME_ACK_PATH = STATE_DIR / "manual_resume.ok"


def guardrail_action(drawdown: float, goal: dict, resume_ack: bool) -> str:
    """Map account drawdown to goal.yaml threshold_actions.

    Max drawdown is a high-water metric over the whole trade history, so a
    breach blocks entries permanently until a human reviews and creates
    state/manual_resume.ok (or archives the history).
    """
    if resume_ack:
        return "normal"
    if drawdown >= float(goal.get("emergency_stop_drawdown", 0.06)):
        return "emergency"
    if drawdown >= float(goal.get("max_drawdown", 0.05)):
        return "halt_entries"
    return "normal"


def market_decision(
    *,
    entry_fired: bool,
    position: dict | None,
    closed_trade: dict | None,
    entry_summary: str,
    current_signal_id: str,
    can_open: bool,
    offline: bool = False,
    guardrail: str = "normal",
) -> dict:
    if offline:
        return {
            "action": "offline_freeze",
            "reason": "Price source is offline fallback; entries and exits are frozen until live data returns.",
            "signal_id": current_signal_id,
        }
    if closed_trade:
        return {
            "action": "close_position",
            "reason": f"Closed paper position via {closed_trade['exit_reason']} at pnl {closed_trade['pnl_pct'] * 100:.2f}%.",
            "signal_id": current_signal_id,
        }
    if guardrail == "emergency":
        return {
            "action": "guardrail_halt",
            "reason": "Emergency stop drawdown reached; trading halted. Create state/manual_resume.ok after manual review to resume.",
            "signal_id": current_signal_id,
        }
    if guardrail == "halt_entries":
        return {
            "action": "guardrail_halt",
            "reason": "Max drawdown reached; new entries are halted pending review (create state/manual_resume.ok to resume).",
            "signal_id": current_signal_id,
        }
    if can_open:
        return {
            "action": "open_position",
            "reason": f"DSL entry triggered: {entry_summary}.",
            "signal_id": current_signal_id,
        }
    if position:
        return {
            "action": "manage_position",
            "reason": "Position remains open; stop loss, take profit, max hold, and DSL exit are not hit.",
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
        "reason": f"No long entry: {entry_summary}.",
        "signal_id": current_signal_id,
    }


async def run_loop(goal: dict, *, iterations: int | None = None) -> None:
    # Local import breaks the loop<->executor cycle: executor.py wraps the
    # trade functions defined above, so it can only be imported once this
    # module is fully loaded.
    from hermes_trading.executor import PaperExecutor
    from hermes_trading.external.orchestrator import signal_source

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    executor: "PaperExecutor" = PaperExecutor()
    log_event("worker_boot", "Booting hermes-trading worker", asset=goal.get("asset", "BTC/USDT"))
    consecutive_failures = 0
    interval = int(os.getenv("HERMES_LOOP_INTERVAL_SECONDS", "60"))
    last_price_source: str | None = None
    last_guardrail: str | None = None
    completed = 0

    while True:
        try:
            strategy = yaml.safe_load(STRATEGY_PATH.read_text()) or {}
            if not is_dsl_strategy(strategy):
                strategy = migrate_strategy_file(strategy)
                atomic_write_text(STRATEGY_PATH, yaml.safe_dump(strategy, sort_keys=False))
                log_event(
                    "strategy_migrated",
                    f"strategy v{strategy.get('version')} migrated on disk from legacy scalars to DSL format",
                    version=strategy.get("version"),
                )
            asset = goal.get("asset", "BTC/USDT")
            market, chain, headline, macro_data = await asyncio.gather(
                _retry("price", lambda: price.fetch(asset)),
                _retry("onchain", onchain.fetch),
                _retry("news", news.fetch),
                _retry("macro", macro.fetch),
            )

            closes = market["closes"]
            candles = market_candles(market)
            rsi = telemetry_rsi(candles)
            regime = rolling_return_regime(closes)
            offline = price_is_offline(market)
            if market["source"] != last_price_source:
                if last_price_source is not None:
                    log_event(
                        "price_source_changed",
                        f"price source switched from {last_price_source} to {market['source']}",
                        previous=last_price_source,
                        current=market["source"],
                    )
                last_price_source = market["source"]
            entry_eval = entry_signal_fired(strategy, candles, market)
            exit_eval = exit_signal_fired(strategy, candles)
            groups = strategy_dsl_groups(strategy)
            entry_summary = dsl_evaluator.summarize(groups["entry"], entry_eval) if entry_eval["details"] else "offline or non-long strategy"
            exit_summary = dsl_evaluator.summarize(groups["exit"], exit_eval)
            entry_fired = entry_eval["triggered"]
            # In tradingview_external mode TradingView owns entries AND signal
            # exits; the worker becomes a pure RISK supervisor over whatever the
            # external orchestrator opened. It must never open a native position
            # nor act on a native DSL exit -- otherwise two engines fight over
            # the same book. stop_loss / take_profit / max_hold / emergency_stop
            # still run, reusing the exact native risk path below.
            external_mode = signal_source(goal) == "tradingview_external"
            dsl_exit_triggered = False if external_mode else exit_eval["triggered"]
            if external_mode:
                entry_fired = False
            dsl_errors = entry_eval["errors"] + exit_eval["errors"]
            if dsl_errors:
                # Same surfacing in live and backtest: errors reach the event
                # log and the heartbeat, never silently force a False.
                log_event("dsl_error", "; ".join(dsl_errors))
            current_signal_id = signal_id(asset, strategy, market)
            position = _load_open_position()
            opened_position = False
            trade_closed = False
            all_trades = _load_recent_trades(limit=None)
            drawdown = max_drawdown(all_trades, goal)
            guard = guardrail_action(drawdown, goal, RESUME_ACK_PATH.exists())
            if guard != last_guardrail:
                if last_guardrail is not None:
                    log_event(
                        "guardrail_changed",
                        f"guardrail state moved from {last_guardrail} to {guard} (drawdown {drawdown:.4f})",
                        previous=last_guardrail,
                        current=guard,
                        drawdown=drawdown,
                    )
                last_guardrail = guard

            if position and not offline and guard == "emergency":
                closed_trade = executor.force_close(
                    position=position, strategy=strategy, market=market, rsi=rsi, regime=regime, reason="emergency_stop"
                )
            elif position and not offline:
                closed_trade = executor.close(
                    position=position, strategy=strategy, market=market, rsi=rsi, regime=regime,
                    exit_triggered=dsl_exit_triggered,
                )
            else:
                closed_trade = None

            if closed_trade and is_duplicate_close(closed_trade, all_trades):
                _quarantine_position(
                    position or {},
                    "a trade with this signal_id is already recorded; discarding duplicate close after crash",
                )
                position = None
                closed_trade = None

            if closed_trade:
                balance_before = compound_balance(all_trades, goal)
                net = float(closed_trade.get("net_pnl_usd", 0.0))
                closed_trade["balance_before_usd"] = balance_before
                closed_trade["balance_after_usd"] = balance_before + net
                closed_trade["account_return"] = net / balance_before if balance_before > 0 else 0.0
                await _append_jsonl(TRADES_PATH, closed_trade)
                POSITION_PATH.unlink(missing_ok=True)
                position = None
                trade_closed = True
                log_event(
                    "trade_closed",
                    f"closed paper trade {asset} via {closed_trade['exit_reason']} pnl_pct={closed_trade['pnl_pct']:.5f}",
                    signal_id=closed_trade.get("signal_id"),
                    exit_reason=closed_trade.get("exit_reason"),
                    net_pnl_usd=closed_trade.get("net_pnl_usd"),
                )

            can_open = (
                entry_fired
                and guard == "normal"
                and position is None
                and not trade_closed
                and should_record_signal(current_signal_id, all_trades[-50:])
            )
            decision = market_decision(
                entry_fired=entry_fired,
                position=position,
                closed_trade=closed_trade,
                entry_summary=entry_summary,
                current_signal_id=current_signal_id,
                can_open=can_open,
                offline=offline,
                guardrail=guard,
            )
            if can_open:
                position = executor.open(
                    asset=asset, strategy=strategy, goal=goal, market=market, rsi=rsi, regime=regime,
                    entry_summary=entry_summary,
                )
                await _write_json(POSITION_PATH, position)
                opened_position = True
                log_event(
                    "position_opened",
                    f"opened paper position {asset} entry={position['entry_price']:.2f}",
                    signal_id=position.get("signal_id"),
                    entry_price=position.get("entry_price"),
                )

            await _write_heartbeat(
                {
                    "ts": _now(),
                    "asset": asset,
                    "last_price": closes[-1],
                    "rsi": rsi,
                    "market_regime": regime,
                    "entry_fired": entry_fired,
                    "drawdown": drawdown,
                    "guardrail": guard,
                    "position_open": position is not None,
                    "opened_position": opened_position,
                    "trade_recorded": trade_closed,
                    "decision_action": decision["action"],
                    "decision_reason": decision["reason"],
                    "signal_id": current_signal_id,
                    "dsl": {
                        "entry_triggered": entry_eval["triggered"],
                        "exit_triggered": exit_eval["triggered"],
                        "entry_summary": entry_summary,
                        "exit_summary": exit_summary,
                        "errors": dsl_errors,
                    },
                    "price_source": market["source"],
                    "onchain_source": chain["source"],
                    "news_source": headline["source"],
                    "macro_source": macro_data["source"],
                }
            )
            consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001 - top-level worker loop must fail closed.
            consecutive_failures += 1
            log_event(
                "worker_failure",
                f"worker failure {consecutive_failures}/5: {exc}",
                consecutive_failures=consecutive_failures,
                error=str(exc),
            )
            if consecutive_failures >= 5:
                log_event("worker_abort", "5 consecutive failures; worker is exiting and needs a restart")
                raise
        completed += 1
        if iterations is not None and completed >= iterations:
            return
        await asyncio.sleep(interval)
