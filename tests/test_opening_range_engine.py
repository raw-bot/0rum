from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from orum.strategies.base import Side, StrategyContext
from orum.strategies.opening_range import OpeningRangeEngine

NEW_YORK = ZoneInfo("America/New_York")


def _ts(hour: int, minute: int, *, day: int = 3) -> int:
    local = datetime(2026, 8, day, hour, minute, tzinfo=NEW_YORK)
    return int(local.astimezone(timezone.utc).timestamp() * 1000)


def _bar(hour: int, minute: int, open_px: float, high: float, low: float, close: float) -> dict:
    return {
        "ts": _ts(hour, minute),
        "open": open_px,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000.0,
    }


def _daily(atr_size: float = 10.0) -> list[dict]:
    rows = []
    for day in range(1, 16):
        close = 100.0 + day * 0.1
        rows.append({
            "ts": day * 86_400_000,
            "open": close,
            "high": close + atr_size / 2,
            "low": close - atr_size / 2,
            "close": close,
            "volume": 1_000_000.0,
        })
    return rows


def _context(current: dict, *, daily: list[dict] | None = None, prior: list[dict] | None = None):
    opening = [
        _bar(9, 30, 100.0, 101.0, 99.0, 100.0),
        _bar(9, 35, 100.0, 102.0, 100.0, 101.0),
        _bar(9, 40, 101.0, 101.5, 100.0, 101.0),
    ]
    candles = opening + list(prior or []) + [current]
    return StrategyContext(
        candles=candles,
        symbol="NVDA",
        timeframe="5m",
        candles_by_timeframe={"1d": daily or _daily()},
    )


def _engine() -> OpeningRangeEngine:
    engine = OpeningRangeEngine()
    engine.init({})
    return engine


def test_high_sweep_back_inside_emits_short_with_range_target():
    current = _bar(9, 45, 101.0, 103.0, 100.5, 101.5)

    signal = _engine().on_candle(current, _context(current))

    assert signal is not None
    assert signal.side == Side.SHORT
    assert signal.suggested_stop == 103.0
    assert signal.suggested_take_profit == 99.0
    assert signal.strategy_metadata["range_atr_ratio"] >= 0.25


def test_low_sweep_back_inside_emits_long_with_range_target():
    current = _bar(9, 45, 100.0, 101.0, 98.0, 100.0)

    signal = _engine().on_candle(current, _context(current))

    assert signal is not None
    assert signal.side == Side.LONG
    assert signal.suggested_stop == 98.0
    assert signal.suggested_take_profit == 102.0


def test_daily_atr_filter_blocks_small_opening_range():
    current = _bar(9, 45, 101.0, 103.0, 100.5, 101.5)

    signal = _engine().on_candle(current, _context(current, daily=_daily(atr_size=20.0)))

    assert signal is None


def test_only_first_reversal_of_session_can_signal():
    first = _bar(9, 45, 101.0, 103.0, 100.5, 101.5)
    current = _bar(9, 50, 101.0, 103.2, 100.5, 101.4)

    signal = _engine().on_candle(current, _context(current, prior=[first]))

    assert signal is None


def test_signal_is_blocked_after_entry_window():
    current = _bar(11, 0, 101.0, 103.0, 100.5, 101.5)

    assert _engine().on_candle(current, _context(current)) is None


def test_last_regular_bar_requests_session_close():
    current = _bar(15, 55, 101.0, 102.0, 100.0, 101.0)

    signal = _engine().on_candle(current, _context(current))

    assert signal is not None
    assert signal.side == Side.EXIT
    assert signal.entry_reason == "opening_range_session_close"
