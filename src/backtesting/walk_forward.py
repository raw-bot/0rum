"""Walk-forward window construction, WFE calculation, and trade simulation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

import numpy as np
import structlog

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
    SL = "sl"
    OPEN = "open"


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

    Returns 0.0 when there are no losers (degenerate — not treated as profitable).

    Args:
        pnl_array: 1-D numpy array of per-trade P&L floats.

    Returns:
        Profit factor >= 0.0. Values > 1.0 indicate net profitability.
    """
    winners = pnl_array[pnl_array > 0]
    losers = pnl_array[pnl_array < 0]
    if len(losers) == 0 or losers.sum() == 0:
        return 0.0
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


def simulate_trade_outcome(
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float | None,
    subsequent_candles: list,
) -> tuple[TradeOutcome, float]:
    """Simulate trade outcome from subsequent candles (oldest-first).

    Checks each candle's high and low to determine which price level is hit
    first. SL takes priority over TP within the same candle.

    direction must be "BUY" or "SELL" — matching Direction.value from CandidateSignal.

    Args:
        direction: "BUY" or "SELL".
        entry: Entry price as float.
        sl: Stop-loss price as float.
        tp1: Take-profit 1 price as float.
        tp2: Take-profit 2 price as float, or None.
        subsequent_candles: Candle ORM/MagicMock objects ordered oldest-newest.
            Each must have .high and .low as Decimal or float-castable.

    Returns:
        Tuple of (TradeOutcome, pnl_in_price_units). pnl is positive for
        winners and negative for losers. OPEN returns pnl=0.0.
    """
    risk = abs(entry - sl)

    for candle in subsequent_candles:
        high = float(candle.high)
        low = float(candle.low)

        if direction == "BUY":
            if low <= sl:
                return TradeOutcome.SL, -risk
            if tp2 is not None and high >= tp2:
                return TradeOutcome.TP2, abs(tp2 - entry)
            if high >= tp1:
                return TradeOutcome.TP1, abs(tp1 - entry)
        else:  # SELL
            if high >= sl:
                return TradeOutcome.SL, -risk
            if tp2 is not None and low <= tp2:
                return TradeOutcome.TP2, abs(tp2 - entry)
            if low <= tp1:
                return TradeOutcome.TP1, abs(tp1 - entry)

    return TradeOutcome.OPEN, 0.0
