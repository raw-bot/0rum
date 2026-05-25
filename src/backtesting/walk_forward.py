"""Walk-forward window construction, WFE calculation, and trade simulation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

import numpy as np
import structlog

from src.backtesting.execution_costs import calculate_trade_accounting
from src.indicators.atr import atr_wilder

log = structlog.get_logger(__name__)

TRADING_DAYS_PER_MONTH: int = 21

MIN_CANDLES_FOR_OPTIMIZER: dict[str, int] = {
    "D1": 257,
    "H4": 1542,
    "H1": 6171,
    "M15": 24685,
}


@dataclass
class WalkForwardWindow:
    """One rolling walk-forward window (train + OOS periods).

    Attributes:
        window_num: 1-based index, oldest window = 1.
        train_start: Start of in-sample training period.
        train_end: End of in-sample period (= oos_start).
        oos_start: Start of out-of-sample evaluation period.
        oos_end: End of out-of-sample evaluation period.
    """

    window_num: int
    train_start: datetime
    train_end: datetime
    oos_start: datetime
    oos_end: datetime


class TradeOutcome(Enum):
    """Possible outcome of a simulated trade."""

    TP1 = "tp1"
    TP2 = "tp2"
    TRAIL = "trail"
    SL = "sl"
    EXPIRED = "expired"
    OPEN = "open"


@dataclass(frozen=True)
class SimulatedSignalOutcome:
    """Cost-adjusted simulated outcome with legacy two-value unpacking support."""

    outcome: TradeOutcome
    pnl: float
    pnl_usd: Decimal
    pnl_pct: Decimal
    closed_at: datetime | None

    def __iter__(self):
        yield self.outcome
        yield self.pnl


def build_windows(
    most_recent_ts: datetime,
    train_months: int,
    test_months: int,
    n_windows: int,
) -> list[WalkForwardWindow]:
    """Build n_windows rolling walk-forward windows anchored at most_recent_ts.

    Window 1 (oldest): OOS ends at most_recent_ts - (n_windows-1) * test_days.
    Window n_windows (most recent): OOS ends at most_recent_ts.
    Each window shifts back by test_days so OOS periods never overlap.

    Args:
        most_recent_ts: Timestamp of the most recent candle in DB.
        train_months: In-sample months (6 per CONTEXT.md).
        test_months: OOS months (2 per CONTEXT.md).
        n_windows: Number of rolling windows (3 per CONTEXT.md).

    Returns:
        List of WalkForwardWindow ordered oldest-first (window_num=1 first).
    """
    train_days = train_months * TRADING_DAYS_PER_MONTH
    test_days = test_months * TRADING_DAYS_PER_MONTH

    windows: list[WalkForwardWindow] = []
    for w in range(n_windows - 1, -1, -1):
        oos_end = most_recent_ts - timedelta(days=w * test_days)
        oos_start = oos_end - timedelta(days=test_days)
        train_end = oos_start
        train_start = train_end - timedelta(days=train_days)
        windows.append(
            WalkForwardWindow(
                window_num=n_windows - w,
                train_start=train_start,
                train_end=train_end,
                oos_start=oos_start,
                oos_end=oos_end,
            )
        )
    return windows


def _compute_profit_factor(pnl_array: np.ndarray) -> float:
    """Compute profit factor = sum(winners) / abs(sum(losers)).

    Returns infinity when there are winners and no losers.

    Args:
        pnl_array: 1-D numpy array of per-trade P&L floats.

    Returns:
        Profit factor >= 0.0. Values > 1.0 indicate net profitability.
    """
    winners = pnl_array[pnl_array > 0]
    losers = pnl_array[pnl_array < 0]
    if len(losers) == 0 or losers.sum() == 0:
        return float("inf") if len(winners) > 0 and winners.sum() > 0 else 0.0
    return float(winners.sum() / abs(losers.sum()))


def _compute_wfe(is_pf: float, oos_pf: float) -> float:
    """Compute Walk-Forward Efficiency = oos_pf / is_pf.

    Returns 0.0 if is_pf is 0 to guard against ZeroDivisionError.

    Args:
        is_pf: In-sample profit factor.
        oos_pf: Out-of-sample profit factor.

    Returns:
        WFE value. Values >= 0.50 pass the gate (wfe_minimum from config).
    """
    if is_pf == 0.0:
        return 0.0
    if np.isinf(is_pf) and np.isinf(oos_pf):
        return float("inf")
    return oos_pf / is_pf


def multi_window_gate_passes(
    oos_profit_factors: list[float],
    min_profitable_windows: int = 2,
) -> bool:
    """Return True if at least min_profitable_windows OOS windows have PF > 1.0.

    Args:
        oos_profit_factors: OOS profit factor from each walk-forward window.
        min_profitable_windows: Minimum count required (2 per CONTEXT.md).

    Returns:
        True if gate passes, False otherwise.
    """
    profitable = sum(1 for pf in oos_profit_factors if pf > 1.0)
    return profitable >= min_profitable_windows


def _check_sufficient_data(candles_by_tf: dict[str, list]) -> bool:
    """Return True only if all timeframes meet the 3-window minimum candle count.

    Logs a structured WARNING for each timeframe that is below minimum.
    The optimizer must call this before attempting any window evaluation.

    Args:
        candles_by_tf: Dict of timeframe string → list of candle objects.

    Returns:
        True if all timeframes have sufficient data; False if any falls short.
    """
    for tf, minimum in MIN_CANDLES_FOR_OPTIMIZER.items():
        count = len(candles_by_tf.get(tf, []))
        if count < minimum:
            log.warning(
                "optimizer.insufficient_data",
                timeframe=tf,
                have=count,
                need=minimum,
            )
            return False
    return True


def _calculate_atr(candles: list, period: int = 14) -> float:
    """Compute shared Wilder ATR for trade simulation."""
    if len(candles) < period + 1:
        return 0.0
    return atr_wilder(candles, period=period)


def _h1_atr_asof(h1_candles: list, timestamp: datetime, period: int = 14) -> float:
    """Return ATR(H1) using candles available at or before timestamp."""
    available = [c for c in h1_candles if c.timestamp <= timestamp]
    return _calculate_atr(available[-20:], period=period)


def simulate_signal_mode_trade_outcome(
    direction: str,
    entry: Decimal,
    sl: Decimal,
    tp1: Decimal,
    tp2: Decimal | None,
    subsequent_m15_candles: list,
    h1_candles: list,
    size_lots: Decimal,
    equity_at_open: Decimal,
    contract_size: Decimal,
    spread_usd: Decimal,
    slippage_usd: Decimal,
    opened_at: datetime | None = None,
    trade_expiry_hours: int | None = None,
) -> SimulatedSignalOutcome:
    """Simulate the signal-mode lifecycle used by live theoretical monitoring.

    Mirrors src.scheduler.jobs._process_trade/_close_trade:
    - M15 candle high/low determines touches.
    - Before TP1, SL wins same-candle ties.
    - At TP1, half is considered realized and an ATR(H1) trailing stop starts.
    - After TP1, trailing stop ratchets and wins ties against TP2.
    - P&L is returned as account return, matching TradeORM.pnl_pct.
    """
    if equity_at_open <= 0 or size_lots <= 0:
        return SimulatedSignalOutcome(
            outcome=TradeOutcome.OPEN,
            pnl=0.0,
            pnl_usd=Decimal("0.00"),
            pnl_pct=Decimal("0.00000"),
            closed_at=None,
        )

    status = "OPEN"
    trailing_stop: float | None = None

    sl_level = float(sl)
    tp1_level = float(tp1)
    tp2_level = float(tp2) if tp2 is not None else None

    def sl_touched(candle) -> bool:
        return (
            float(candle.low) <= sl_level
            if direction == "BUY"
            else float(candle.high) >= sl_level
        )

    def tp1_touched(candle) -> bool:
        return (
            float(candle.high) >= tp1_level
            if direction == "BUY"
            else float(candle.low) <= tp1_level
        )

    def tp2_touched(candle) -> bool:
        if tp2_level is None:
            return False
        return (
            float(candle.high) >= tp2_level
            if direction == "BUY"
            else float(candle.low) <= tp2_level
        )

    def trail_touched(candle) -> bool:
        if trailing_stop is None:
            return False
        return (
            float(candle.low) <= trailing_stop
            if direction == "BUY"
            else float(candle.high) >= trailing_stop
        )

    def close_result(
        exit_price: Decimal,
        outcome: TradeOutcome,
        closed_at: datetime | None,
    ) -> SimulatedSignalOutcome:
        accounting = calculate_trade_accounting(
            direction=direction,
            entry_price=entry,
            exit_price=exit_price,
            tp1_price=tp1,
            size_lots=size_lots,
            equity_base=equity_at_open,
            contract_size=contract_size,
            spread_usd=spread_usd,
            slippage_usd=slippage_usd,
            close_reason=outcome.name,
            original_status=status,
        )
        return SimulatedSignalOutcome(
            outcome=outcome,
            pnl=float(accounting.pnl_pct),
            pnl_usd=accounting.pnl_usd,
            pnl_pct=accounting.pnl_pct,
            closed_at=closed_at,
        )

    expiry_at = (
        opened_at + timedelta(hours=trade_expiry_hours)
        if opened_at is not None and trade_expiry_hours is not None
        else None
    )

    for candle in subsequent_m15_candles:
        if expiry_at is not None and candle.timestamp >= expiry_at:
            return close_result(
                Decimal(str(candle.close)),
                TradeOutcome.EXPIRED,
                candle.timestamp,
            )

        if status == "OPEN":
            if sl_touched(candle):
                return close_result(sl, TradeOutcome.SL, candle.timestamp)
            if tp1_touched(candle):
                status = "TP1_HIT"
                atr_h1 = _h1_atr_asof(h1_candles, candle.timestamp)
                if atr_h1 > 0:
                    trailing_stop = (
                        float(tp1) - atr_h1
                        if direction == "BUY"
                        else float(tp1) + atr_h1
                    )
                continue

        if status == "TP1_HIT":
            atr_h1 = _h1_atr_asof(h1_candles, candle.timestamp)
            if atr_h1 > 0:
                if direction == "BUY":
                    new_trail = float(candle.high) - atr_h1
                    if trailing_stop is None or new_trail > trailing_stop:
                        trailing_stop = new_trail
                else:
                    new_trail = float(candle.low) + atr_h1
                    if trailing_stop is None or new_trail < trailing_stop:
                        trailing_stop = new_trail

            if trail_touched(candle) and trailing_stop is not None:
                return close_result(
                    Decimal(str(trailing_stop)),
                    TradeOutcome.TRAIL,
                    candle.timestamp,
                )
            if tp2_touched(candle) and tp2 is not None:
                return close_result(tp2, TradeOutcome.TP2, candle.timestamp)

    return SimulatedSignalOutcome(
        outcome=TradeOutcome.OPEN,
        pnl=0.0,
        pnl_usd=Decimal("0.00"),
        pnl_pct=Decimal("0.00000"),
        closed_at=None,
    )
