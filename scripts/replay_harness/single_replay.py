"""strategy_legacy replay: the HA engine alone, plus the sizing/fill matrix.

The candidate stream is generated ONCE from rolling `candles_limit` windows —
exactly what the live provider hands the engine — and every policy config
consumes that same stream (§5 of the harness charter). What differs per config
is only what happens to a candidate afterwards: decision, sizing, fill, exit.

Reproduced legacy conventions (deliberately imperfect, see charter):
  * fill at the SIGNAL candle's close, at the first worker cycle after it;
  * sizing on atr_risk = 2 x ATR(14) regardless of the strategy stop;
  * total-risk cap checked before symbol cap, both in atr_risk units;
  * leverage cap applied after sizing, shrinking qty (paper_broker.open);
  * equity snapshot frozen after protective exits, before entries;
  * SL first when SL and TP are touched by the same monitor candle;
  * every 15m monitor candle after the cursor is processed, in order;
  * no slippage, round-trip fee 0.001 split half/half;
  * legacy R stays gross and ATR-based: (exit - entry) / atr_risk.

The non-legacy fill convention (next_available_fill) fills at the first 15m
bar OPEN after decision_available_time + latency, applies configurable
slippage, refuses gap-through-stop entries, and handles exit gaps.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from orum.strategies.base import StrategyContext
from orum.strategies.ha_trend import HaTrendEngine

from scripts.replay_harness.events import EventLog
from scripts.replay_harness.features import _atr, candidate_features, mae_mfe
from scripts.replay_harness.timeline import INTERVAL_MS, SnapshotProvider, timestamps_for_signal

FEE_RT = 0.001  # paper_broker.DEFAULT_FEE_RT

SIZINGS = ("legacy_atr_2", "stop_exact", "stop_floor_1atr", "stop_floor_2atr")
FILLS = ("legacy_close_fill", "next_available_fill")
TARGET_REFS = ("signal_close", "fill")


@dataclass(frozen=True)
class ReplayConfig:
    name: str
    sizing: str = "legacy_atr_2"
    fill: str = "legacy_close_fill"
    target_ref: str = "signal_close"
    risk_pct: float = 0.01
    max_leverage: float | None = 3.0
    max_total_stop_risk_pct: float = 0.05
    max_symbol_stop_risk_pct: float = 0.03
    latency_ms: int = 60_000          # next_available_fill only
    slippage_bps: float = 2.0         # next_available_fill only
    starting_balance: float = 10_000.0

    def __post_init__(self) -> None:
        if self.sizing not in SIZINGS:
            raise ValueError(f"unknown sizing {self.sizing!r}")
        if self.fill not in FILLS:
            raise ValueError(f"unknown fill {self.fill!r}")
        if self.target_ref not in TARGET_REFS:
            raise ValueError(f"unknown target_ref {self.target_ref!r}")


def generate_candidates(provider: SnapshotProvider, *, symbol: str, timeframe: str,
                        params: dict | None = None, candles_limit: int = 300,
                        start_ms: int | None = None, end_ms: int | None = None) -> list[dict]:
    """Run the real HaTrendEngine over rolling as_of windows; one candidate per
    emitted signal. Identical for every policy config downstream."""
    engine = HaTrendEngine()
    engine.init(params or {})
    step = INTERVAL_MS[timeframe]
    bars = provider.series[(symbol, timeframe)]
    out: list[dict] = []
    for i in range(len(bars)):
        bar = bars[i]
        knowable = bar["ts"] + step
        if start_ms is not None and bar["ts"] < start_ms:
            continue
        if end_ms is not None and knowable > end_ms:
            break
        window = provider.as_of(symbol, timeframe, candles_limit, knowable)
        if len(window) < engine.warmup_period or window[-1]["ts"] != bar["ts"]:
            continue
        signal = engine.on_candle(window[-1], StrategyContext(
            candles=window, symbol=symbol, timeframe=timeframe))
        if signal is None:
            continue
        close = float(window[-1]["close"])
        stop = float(signal.suggested_stop)
        rr = float(signal.strategy_metadata.get("rr", 3.0))
        out.append({
            **timestamps_for_signal(bar["ts"], timeframe),
            "symbol": symbol,
            "timeframe": timeframe,
            "side": "long",
            "signal_close": close,
            "stop": stop,
            "suggested_take_profit": float(signal.suggested_take_profit),
            "rr": rr,
            "atr_risk": 2.0 * _atr(window),          # legacy sizing basis
            "entry_reason": signal.entry_reason,
            "features": candidate_features(window, stop=stop),
        })
    return out


def _sizing_basis(config: ReplayConfig, *, stop_distance: float, atr_risk: float) -> float | None:
    atr_1 = atr_risk / 2.0
    basis = {
        "legacy_atr_2": atr_risk,
        "stop_exact": stop_distance,
        "stop_floor_1atr": max(stop_distance, atr_1),
        "stop_floor_2atr": max(stop_distance, atr_risk),
    }[config.sizing]
    if not (math.isfinite(basis) and basis > 0):
        return None
    return basis


def _entry_fill(config: ReplayConfig, candidate: dict, monitor: list[dict]) -> dict | None:
    """Where/when this config actually fills. None = no fill possible (end of
    data). Returns fill price BEFORE validity checks."""
    if config.fill == "legacy_close_fill":
        return {"price": candidate["signal_close"],
                "fill_time": candidate["cycle_observed_time"],
                "price_source_time": candidate["bar_close_time"],
                "slippage_bps_applied": 0.0}
    earliest = candidate["decision_available_time"] + config.latency_ms
    for bar in monitor:
        if bar["ts"] >= earliest:
            raw = float(bar["open"])
            price = raw * (1.0 + config.slippage_bps / 10_000.0)
            return {"price": price, "fill_time": bar["ts"],
                    "price_source_time": bar["ts"],
                    "slippage_bps_applied": config.slippage_bps}
    return None


def _protective_exit(config: ReplayConfig, bar: dict, *, stop: float, target: float) -> dict | None:
    """Legacy: frozen-level fills, SL first. Realistic fill mode adds
    gap-through handling and exit slippage on stops."""
    low, high, opn = float(bar["low"]), float(bar["high"]), float(bar["open"])
    slip = config.slippage_bps / 10_000.0 if config.fill == "next_available_fill" else 0.0
    sl_hit = low <= stop
    tp_hit = high >= target
    if sl_hit:
        gapped = opn <= stop
        raw = opn if (gapped and config.fill == "next_available_fill") else stop
        return {"reason": "stop_loss", "price": raw * (1.0 - slip),
                "collision": bool(tp_hit), "gap_through": gapped}
    if tp_hit:
        raw = opn if opn >= target else target  # favourable gap fills at open
        return {"reason": "take_profit", "price": raw, "collision": False,
                "gap_through": opn >= target}
    return None


def run_replay(config: ReplayConfig, candidates: list[dict], provider: SnapshotProvider,
               *, symbol: str, monitor_timeframe: str = "15m",
               event_log: EventLog | None = None) -> dict:
    """Consume the shared candidate stream under one policy config.
    Returns {trades, equity_curve, decisions, config}."""
    monitor = provider.series[(symbol, monitor_timeframe)]
    step = INTERVAL_MS[monitor_timeframe]

    balance = config.starting_balance
    position: dict | None = None
    pending = sorted(candidates, key=lambda c: c["cycle_observed_time"])
    next_candidate = 0
    trades: list[dict] = []
    decisions: list[dict] = []
    equity_curve: list[tuple[int, float]] = []

    def decide(candidate: dict, action: str, reason: str, **extra) -> None:
        record = {"candidate_ts": candidate["bar_open_time"], "action": action,
                  "reason": reason, **extra}
        decisions.append(record)
        if event_log:
            event_log.emit("portfolio_decision", config=config.name, **record)

    for bar in monitor:
        bar_close = bar["ts"] + step
        closed_this_bar = False

        # -- protective exits first (paper_engine phase 2a) ------------------
        if position is not None and bar["ts"] >= position["monitor_cursor"]:
            hit = _protective_exit(config, bar, stop=position["stop"], target=position["target"])
            position["monitor_cursor"] = bar["ts"] + 1
            if hit is not None:
                exit_price = hit["price"]
                qty = position["qty"]
                gross = qty * (exit_price - position["entry_price"])
                exit_fee = qty * exit_price * FEE_RT / 2
                balance += gross - exit_fee
                worst, best = mae_mfe(monitor, entry_price=position["entry_price"],
                                      entry_ms=position["fill_time"], exit_ms=bar["ts"])
                stop_dist = position["entry_price"] - position["stop"]
                trade = {
                    **position["candidate_snapshot"],
                    "config": config.name,
                    "entry_price": position["entry_price"],
                    "exit_price": exit_price,
                    "exit_time": bar["ts"],
                    "exit_reason": hit["reason"],
                    "sl_tp_collision": hit["collision"],
                    "gap_through": hit["gap_through"],
                    "qty": qty,
                    "notional_usd": position["notional"],
                    "holding_ms": bar["ts"] - position["fill_time"],
                    "entry_fee": position["entry_fee"],
                    "exit_fee": exit_fee,
                    "total_fees": position["entry_fee"] + exit_fee,
                    "gross_pnl_usd": gross,
                    "account_net_pnl": gross - exit_fee - position["entry_fee"],
                    "risk_basis_atr_usd": qty * position["atr_risk"],
                    "risk_basis_stop_usd": qty * stop_dist if stop_dist > 0 else float("nan"),
                    "r_legacy_gross": (exit_price - position["entry_price"]) / position["atr_risk"],
                    "r_atr_net": (gross - exit_fee - position["entry_fee"]) / (qty * position["atr_risk"]),
                    "r_stop_net": ((gross - exit_fee - position["entry_fee"]) / (qty * stop_dist)
                                   if stop_dist > 0 else float("nan")),
                    "mae_frac": worst,
                    "mfe_frac": best,
                }
                trades.append(trade)
                if event_log:
                    event_log.emit("exit", config=config.name, **{
                        k: trade[k] for k in ("bar_open_time", "exit_time", "exit_reason",
                                              "exit_price", "sl_tp_collision", "gap_through",
                                              "account_net_pnl", "r_legacy_gross",
                                              "r_atr_net", "r_stop_net", "total_fees")})
                position = None
                closed_this_bar = True

        # -- entries (paper_engine phase 2b; no re-entry after a same-cycle exit)
        while next_candidate < len(pending) and pending[next_candidate]["cycle_observed_time"] <= bar_close:
            candidate = pending[next_candidate]
            next_candidate += 1
            if position is not None:
                decide(candidate, "rejected", "already_holding")
                continue
            if closed_this_bar:
                decide(candidate, "rejected", "protectively_closed_this_cycle")
                continue

            fill = _entry_fill(config, candidate, monitor)
            if fill is None:
                decide(candidate, "rejected", "no_fill_bar_available")
                continue
            entry_price = fill["price"]
            stop = candidate["stop"]
            if not (math.isfinite(stop) and stop > 0 and stop < entry_price):
                decide(candidate, "rejected",
                       "gap_through_stop" if stop >= entry_price else "invalid_stop",
                       stop=stop, fill_price=entry_price)
                continue

            target_ref = candidate["signal_close"] if config.target_ref == "signal_close" else entry_price
            target = target_ref + candidate["rr"] * (target_ref - stop)
            if target <= entry_price:
                decide(candidate, "rejected", "target_below_fill", target=target,
                       fill_price=entry_price)
                continue

            equity = balance  # flat here, so equity == balance (legacy snapshot)
            basis = _sizing_basis(config, stop_distance=entry_price - stop,
                                  atr_risk=candidate["atr_risk"])
            if basis is None:
                decide(candidate, "rejected", "invalid_sizing_basis")
                continue
            candidate_risk_usd = config.risk_pct * equity
            if candidate_risk_usd > config.max_total_stop_risk_pct * equity:
                decide(candidate, "rejected", "risk_cap_total")
                continue
            if candidate_risk_usd > config.max_symbol_stop_risk_pct * equity:
                decide(candidate, "rejected", "risk_cap_symbol")
                continue
            qty = candidate_risk_usd / basis
            leverage_capped = False
            if config.max_leverage and qty * entry_price > config.max_leverage * equity:
                qty = config.max_leverage * equity / entry_price
                leverage_capped = True
            notional = qty * entry_price
            entry_fee = notional * FEE_RT / 2
            balance -= entry_fee
            position = {
                "candidate_snapshot": dict(candidate),
                "entry_price": entry_price,
                "stop": stop,
                "target": target,
                "qty": qty,
                "notional": notional,
                "entry_fee": entry_fee,
                "atr_risk": candidate["atr_risk"],
                "fill_time": fill["fill_time"],
                "monitor_cursor": fill["fill_time"] + 1,
            }
            decide(candidate, "accepted", "open", qty=qty, notional_usd=notional,
                   sizing_basis=basis, leverage_capped=leverage_capped,
                   actual_risk_at_stop_usd=qty * (entry_price - stop))
            if event_log:
                event_log.emit("order", config=config.name, sizing=config.sizing,
                               qty=qty, notional_usd=notional, sizing_basis_usd=basis,
                               leverage_capped=leverage_capped,
                               candidate_ts=candidate["bar_open_time"])
                event_log.emit("fill", config=config.name,
                               candidate_ts=candidate["bar_open_time"], **fill)

        mark = position["qty"] * (float(bar["close"]) - position["entry_price"]) if position else 0.0
        equity_curve.append((bar_close, balance + mark))

    return {"config": asdict(config), "trades": trades, "decisions": decisions,
            "equity_curve": equity_curve, "open_position_at_end": position is not None}
