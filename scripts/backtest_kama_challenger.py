"""Offline KAMA squeeze challenger simulator.

The module is import-safe: network access only occurs from ``main``. Unit tests
inject candles and precomputed indicator state. Entries and confirmed exits fill
at the following bar open; sizing is spot-style 1x with a 0.5% risk budget and
10% notional cap.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from orum.external.bracket import compute_bracket
from orum.external.ak_macd import AkMacdParams, compute_state as compute_ak_state
from orum.strategies.kama_squeeze import (
    KamaSqueezeParams,
    KamaSqueezeState,
    compute_kama_state,
    entry_at,
    open_long_position,
    update_long_position,
)

RISK_FRACTION = 0.005
MAX_ALLOCATION_FRACTION = 0.10
FEE_ROUND_TRIP = 0.001
SLIPPAGE_FRACTION = 0.0003
AK_FLOOR_R = 1.5
AK_RUNNER_R = 2.0
AK_TRAIL_ATR = 2.5


@dataclass
class SimulationResult:
    trades: list[dict]
    final_equity: float
    equity_curve: list[float]


@dataclass(frozen=True)
class AkRunnerState:
    entry_price: float
    risk_distance: float
    stop: float
    peak: float


@dataclass(frozen=True)
class AkEntryPlan:
    signal_index: int
    baseline_at_signal: float
    recent_low: float
    recent_high: float


def advance_ak_runner(
    runner: AkRunnerState,
    *,
    high: float,
    low: float,
    atr: float,
    slippage_fraction: float,
) -> tuple[AkRunnerState, float | None]:
    """Advance the validated long-only AK runner with pessimistic bar order.

    The stop for this bar uses only the peak known before the bar. The stop is
    tested before the current high can raise the peak, avoiding favorable
    intrabar sequence assumptions.
    """
    profit_r = (runner.peak - runner.entry_price) / runner.risk_distance
    if profit_r >= AK_RUNNER_R:
        candidate = runner.peak - AK_TRAIL_ATR * atr
        target = max(runner.entry_price + AK_FLOOR_R * runner.risk_distance, candidate)
    elif profit_r >= AK_FLOOR_R:
        target = runner.entry_price + AK_FLOOR_R * runner.risk_distance
    else:
        target = runner.stop
    stop = max(runner.stop, target)
    updated = AkRunnerState(
        entry_price=runner.entry_price,
        risk_distance=runner.risk_distance,
        stop=stop,
        peak=max(runner.peak, high),
    )
    if low <= stop:
        return updated, stop * (1.0 - slippage_fraction)
    return updated, None


def simulate_ak_plans(
    candles: list[dict],
    *,
    plans: list[AkEntryPlan],
    atr: list[float],
    starting_equity: float = 10_000.0,
    fee_round_trip: float = FEE_ROUND_TRIP,
    slippage_fraction: float = SLIPPAGE_FRACTION,
) -> SimulationResult:
    """Simulate precomputed confirmed AK entries with the validated 4h runner."""
    if len(candles) != len(atr):
        raise ValueError("candles and ATR length differ")
    plans_by_signal = {plan.signal_index: plan for plan in plans}
    equity = float(starting_equity)
    equity_curve = [equity]
    trades: list[dict] = []
    pending_plan: AkEntryPlan | None = None
    runner: AkRunnerState | None = None
    open_trade: dict | None = None

    for i, candle in enumerate(candles):
        if pending_plan is not None:
            entry_price = float(candle["open"]) * (1.0 + slippage_fraction)
            try:
                bracket = compute_bracket(
                    entry_price=entry_price,
                    baseline_at_entry=pending_plan.baseline_at_signal,
                    recent_low=pending_plan.recent_low,
                    recent_high=pending_plan.recent_high,
                    direction="long",
                    rr=2.0,
                )
            except ValueError:
                pending_plan = None
            else:
                risk_distance = entry_price - bracket.stop_loss_price
                risk_limited = equity * RISK_FRACTION * entry_price / risk_distance
                notional = min(equity * MAX_ALLOCATION_FRACTION, risk_limited)
                qty = notional / entry_price
                planned_risk = qty * risk_distance
                entry_fee = notional * fee_round_trip / 2.0
                equity -= entry_fee
                runner = AkRunnerState(
                    entry_price=entry_price,
                    risk_distance=risk_distance,
                    stop=bracket.stop_loss_price,
                    peak=entry_price,
                )
                open_trade = {
                    "signal_index": pending_plan.signal_index,
                    "entry_index": i,
                    "entry_price": entry_price,
                    "qty": qty,
                    "notional_usd": notional,
                    "planned_risk_usd": planned_risk,
                    "entry_fee_usd": entry_fee,
                    "equity_before_usd": equity + entry_fee,
                }
                pending_plan = None

        if runner is not None and open_trade is not None:
            runner, exit_price = advance_ak_runner(
                runner,
                high=float(candle["high"]),
                low=float(candle["low"]),
                atr=atr[i],
                slippage_fraction=slippage_fraction,
            )
            if exit_price is not None:
                gross = open_trade["qty"] * (exit_price - open_trade["entry_price"])
                exit_fee = open_trade["qty"] * exit_price * fee_round_trip / 2.0
                net = gross - open_trade["entry_fee_usd"] - exit_fee
                equity += gross - exit_fee
                trades.append({
                    **open_trade,
                    "exit_index": i,
                    "exit_price": exit_price,
                    "exit_fee_usd": exit_fee,
                    "net_pnl_usd": net,
                    "r": net / open_trade["planned_risk_usd"],
                    "exit_reason": "ak_runner_stop",
                    "equity_after_usd": equity,
                })
                runner = None
                open_trade = None
                equity_curve.append(equity)

        if runner is None and pending_plan is None and i in plans_by_signal and i + 1 < len(candles):
            pending_plan = plans_by_signal[i]

    return SimulationResult(trades=trades, final_equity=equity, equity_curve=equity_curve)


def simulate_ak_macd(
    candles: list[dict],
    *,
    starting_equity: float = 10_000.0,
    fee_round_trip: float = FEE_ROUND_TRIP,
    slippage_fraction: float = SLIPPAGE_FRACTION,
) -> SimulationResult:
    """Build confirmed long-only AK plans with the existing brain, then simulate."""
    params = AkMacdParams(allow_short=False)
    if len(candles) < params.warmup:
        return SimulationResult(trades=[], final_equity=starting_equity, equity_curve=[starting_equity])

    # Reuse the frozen validation implementation instead of re-encoding AK
    # confirmation and ATR semantics in this challenger script.
    from scripts.backtest_4h_validation import atr_series, confirmed_entries

    state = compute_ak_state(candles, params)
    plans: list[AkEntryPlan] = []
    for index, direction in confirmed_entries(state, params):
        if direction != "long":
            continue
        swing = params.swing_look
        plans.append(AkEntryPlan(
            signal_index=index,
            baseline_at_signal=state.baseline[index],
            recent_low=min(state.lows[max(0, index - swing + 1):index + 1]),
            recent_high=max(state.highs[max(0, index - swing + 1):index + 1]),
        ))
    atr = atr_series(state.highs, state.lows, state.closes, params.atr_len)
    return simulate_ak_plans(
        candles,
        plans=plans,
        atr=atr,
        starting_equity=starting_equity,
        fee_round_trip=fee_round_trip,
        slippage_fraction=slippage_fraction,
    )


def size_notional(
    *,
    equity: float,
    entry_price: float,
    signal_atr: float,
    params: KamaSqueezeParams,
    risk_fraction: float = RISK_FRACTION,
    max_allocation_fraction: float = MAX_ALLOCATION_FRACTION,
) -> float:
    if not (equity > 0 and entry_price > 0 and signal_atr > 0):
        return 0.0
    risk_distance = params.initial_stop_atr * signal_atr
    risk_limited = equity * risk_fraction * entry_price / risk_distance
    allocation_limited = equity * max_allocation_fraction
    return min(risk_limited, allocation_limited)


def simulate_kama(
    candles: list[dict],
    *,
    params: KamaSqueezeParams | None = None,
    state: KamaSqueezeState | None = None,
    starting_equity: float = 10_000.0,
    fee_round_trip: float = FEE_ROUND_TRIP,
    slippage_fraction: float = SLIPPAGE_FRACTION,
) -> SimulationResult:
    params = params or KamaSqueezeParams()
    state = state or compute_kama_state(candles, params)
    if len(candles) != len(state.closes):
        raise ValueError("candles and state length differ")

    equity = float(starting_equity)
    equity_curve = [equity]
    trades: list[dict] = []
    position = None
    open_trade: dict | None = None
    pending_entry: int | None = None
    pending_exit: tuple[int, str] | None = None

    for i, candle in enumerate(candles):
        exited_at_open = False
        if pending_exit is not None and position is not None and open_trade is not None:
            exit_signal_index, exit_reason = pending_exit
            exit_price = float(candle["open"]) * (1.0 - slippage_fraction)
            gross = open_trade["qty"] * (exit_price - open_trade["entry_price"])
            exit_fee = open_trade["qty"] * exit_price * fee_round_trip / 2.0
            net = gross - open_trade["entry_fee_usd"] - exit_fee
            equity += gross - exit_fee
            trade = {
                **open_trade,
                "exit_signal_index": exit_signal_index,
                "exit_index": i,
                "exit_price": exit_price,
                "exit_fee_usd": exit_fee,
                "net_pnl_usd": net,
                "r": net / open_trade["planned_risk_usd"],
                "exit_reason": exit_reason,
                "equity_after_usd": equity,
            }
            trades.append(trade)
            position = None
            open_trade = None
            pending_exit = None
            exited_at_open = True
            equity_curve.append(equity)

        if pending_entry is not None:
            entry_price = float(candle["open"]) * (1.0 + slippage_fraction)
            notional = size_notional(
                equity=equity,
                entry_price=entry_price,
                signal_atr=state.atr[pending_entry],
                params=params,
            )
            if notional > 0.0:
                qty = notional / entry_price
                planned_risk = qty * params.initial_stop_atr * state.atr[pending_entry]
                entry_fee = notional * fee_round_trip / 2.0
                equity -= entry_fee
                position = open_long_position(
                    fill_price=entry_price,
                    signal_atr=state.atr[pending_entry],
                    entry_index=i,
                    params=params,
                )
                open_trade = {
                    "signal_index": pending_entry,
                    "entry_index": i,
                    "entry_price": entry_price,
                    "qty": qty,
                    "notional_usd": notional,
                    "planned_risk_usd": planned_risk,
                    "entry_fee_usd": entry_fee,
                    "equity_before_usd": equity + entry_fee,
                }
            pending_entry = None

        if position is not None:
            position, reason = update_long_position(
                position,
                close=state.closes[i],
                atr=state.atr[i],
                kama=state.kama[i],
                momentum=state.momentum[i],
                params=params,
            )
            if reason is not None and i + 1 < len(candles):
                pending_exit = (i, reason)
        elif not exited_at_open and entry_at(state, i, params) and i + 1 < len(candles):
            pending_entry = i

    return SimulationResult(trades=trades, final_equity=equity, equity_curve=equity_curve)


def metrics(result: SimulationResult) -> dict:
    returns = [trade["r"] for trade in result.trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    peak = result.equity_curve[0]
    max_drawdown = 0.0
    for equity in result.equity_curve:
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 0.0)
    return {
        "trades": len(returns),
        "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
        "expectancy_r": sum(returns) / len(returns) if returns else 0.0,
        "net_pnl_usd": result.final_equity - result.equity_curve[0],
        "max_drawdown": max_drawdown,
    }


def _r_metrics(trades: list[dict]) -> dict:
    returns = [float(trade["r"]) for trade in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    return {
        "trades": len(returns),
        "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
        "expectancy_r": sum(returns) / len(returns) if returns else 0.0,
        "net_r": sum(returns),
    }


def chronological_metrics(trades: list[dict], candles: list[dict], *, split_year: int) -> dict:
    def year_for(trade: dict) -> int:
        raw = float(candles[int(trade["signal_index"])]["ts"])
        seconds = raw / 1000.0 if raw > 10_000_000_000 else raw
        return datetime.fromtimestamp(seconds, timezone.utc).year

    in_sample = [trade for trade in trades if year_for(trade) < split_year]
    out_of_sample = [trade for trade in trades if year_for(trade) >= split_year]
    years = sorted({year_for(trade) for trade in trades})
    return {
        "split_year": split_year,
        "in_sample": _r_metrics(in_sample),
        "out_of_sample": _r_metrics(out_of_sample),
        "by_year": {
            str(year): _r_metrics([trade for trade in trades if year_for(trade) == year])
            for year in years
        },
    }


def _load_json_candles(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, list):
        raise ValueError("candle file must contain a JSON list")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare inactive KAMA squeeze with AK-MACD")
    parser.add_argument("candles", type=Path, help="JSON list of normalized oldest-to-newest candles")
    parser.add_argument("--split-year", type=int, default=2024)
    args = parser.parse_args()
    candles = _load_json_candles(args.candles)
    kama = simulate_kama(candles)
    ak = simulate_ak_macd(candles)
    comparison = {
        "kama_squeeze": {
            "full": metrics(kama),
            "chronological": chronological_metrics(kama.trades, candles, split_year=args.split_year),
        },
        "ak_macd": {
            "full": metrics(ak),
            "chronological": chronological_metrics(ak.trades, candles, split_year=args.split_year),
        },
    }
    print(json.dumps(comparison, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
