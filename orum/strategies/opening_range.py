"""NVDA 15-minute opening-range reversal for isolated paper execution."""

from __future__ import annotations

from datetime import datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo

from orum.market_calendar import session_bounds
from orum.strategies.base import Side, Signal, StrategyContext

NEW_YORK = ZoneInfo("America/New_York")


def _local_datetime(candle: dict) -> datetime:
    return datetime.fromtimestamp(float(candle["ts"]) / 1000, tz=timezone.utc).astimezone(NEW_YORK)


def atr14(candles: list[dict]) -> float | None:
    ordered = sorted(candles, key=lambda row: float(row["ts"]))
    if len(ordered) < 15:
        return None
    true_ranges: list[float] = []
    previous_close = float(ordered[0]["close"])
    for row in ordered[1:]:
        high = float(row["high"])
        low = float(row["low"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = float(row["close"])
    return sum(true_ranges[-14:]) / 14 if len(true_ranges) >= 14 else None


def _reversal_side(candle: dict, range_low: float, range_high: float) -> Side | None:
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])
    inside = range_low < close < range_high
    swept_high = high > range_high and inside
    swept_low = low < range_low and inside
    if swept_high == swept_low:
        return None
    return Side.SHORT if swept_high else Side.LONG


class OpeningRangeEngine:
    name = "opening_range"
    version = "nvda_15m_reversal_v2"
    required_timeframes = ["1d"]
    required_indicators = ["opening_range_15m", "atr14_d1"]
    warmup_period = 15

    def init(self, config: dict) -> None:
        self.min_range_atr_ratio = float(config.get("min_range_atr_ratio", 0.25))
        if not 0 < self.min_range_atr_ratio <= 2:
            raise ValueError("min_range_atr_ratio must be in (0, 2]")

    def on_candle(self, candle: dict, context: StrategyContext) -> Signal | None:
        self.skipped_signal_ts = None
        if context.symbol != "NVDA" or context.timeframe != "5m":
            return None
        current = _local_datetime(candle)
        current_clock = current.time().replace(tzinfo=None)
        session = session_bounds(current.date())
        if session is None:
            return None
        # Signal at the close of the 15:40/12:40 bar, leaving three M5 polls
        # before the exchange closes (observed-mark execution).
        if current + timedelta(minutes=5) >= session[1] - timedelta(minutes=15):
            return Signal(
                side=Side.EXIT,
                symbol=context.symbol,
                timeframe=context.timeframe,
                entry_reason="opening_range_session_close",
            )
        same_day = [
            row for row in context.candles
            if _local_datetime(row).date() == current.date()
        ]
        opening = [
            row for row in same_day
            if time(9, 30) <= _local_datetime(row).time().replace(tzinfo=None) < time(9, 45)
        ]
        expected = {time(9, 30), time(9, 35), time(9, 40)}
        observed = {_local_datetime(row).time().replace(tzinfo=None) for row in opening}
        if observed != expected:
            return None
        range_high = max(float(row["high"]) for row in opening)
        range_low = min(float(row["low"]) for row in opening)
        range_width = range_high - range_low
        atr = atr14(context.candles_by_timeframe.get("1d", []))
        if atr is None or atr <= 0 or range_width / atr < self.min_range_atr_ratio:
            return None

        entry_rows = [
            row for row in same_day
            if time(9, 45) <= _local_datetime(row).time().replace(tzinfo=None) < min(current_clock, time(11, 0))
        ]
        first_reversal = next((row for row in entry_rows if _reversal_side(row, range_low, range_high) is not None), None)
        if first_reversal is not None:
            self.skipped_signal_ts = first_reversal["ts"]
            return None
        if not time(9, 45) <= current_clock < time(11, 0):
            return None
        side = _reversal_side(candle, range_low, range_high)
        if side is None:
            return None
        stop = float(candle["low"] if side == Side.LONG else candle["high"])
        target = range_high if side == Side.LONG else range_low
        return Signal(
            side=side,
            symbol=context.symbol,
            timeframe=context.timeframe,
            entry_reason=f"opening_range_reversal_{side.value}",
            suggested_stop=stop,
            suggested_take_profit=target,
            strategy_metadata={
                "range_low": range_low,
                "range_high": range_high,
                "range_width": range_width,
                "atr14_d1": atr,
                "range_atr_ratio": range_width / atr,
                "session_date": current.date().isoformat(),
            },
        )


ENGINE_CLASS = OpeningRangeEngine
