"""Read-only market snapshot backing the Opening Range operator page."""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from orum.adapters.us_equity import (
    SOURCE_GRADE,
    SOURCE_NAME,
    UsEquityDataError,
    fetch_us_equity_candles,
)
from orum.strategies.base import StrategyContext
from orum.strategies.opening_range import OpeningRangeEngine, atr14

NEW_YORK = ZoneInfo("America/New_York")


def _local(candle: dict) -> datetime:
    return datetime.fromtimestamp(float(candle["ts"]) / 1000, tz=timezone.utc).astimezone(NEW_YORK)


def _empty_snapshot(error: str) -> dict:
    return {
        "status": "UNAVAILABLE",
        "connected": False,
        "provider": SOURCE_NAME,
        "grade": SOURCE_GRADE,
        "symbol": "NVDA",
        "timeframes": {"5m": 0, "1d": 0},
        "latest": None,
        "opening_range": None,
        "atr14_d1": None,
        "signal": None,
        "candles": [],
        "fresh": False,
        "freshness_seconds": None,
        "error": error,
    }


def opening_range_snapshot(*, now: datetime | None = None) -> dict:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        m5 = fetch_us_equity_candles("NVDA", "5m", 300, now=observed_at)
        daily = fetch_us_equity_candles("NVDA", "1d", 30, now=observed_at)
    except UsEquityDataError as exc:
        return _empty_snapshot(str(exc))
    except Exception as exc:  # noqa: BLE001 - source failure must remain visible, not kill dashboard.
        return _empty_snapshot(f"{type(exc).__name__}: {exc}")

    latest = m5[-1]
    latest_local = _local(latest)
    now_local = observed_at.astimezone(NEW_YORK)
    freshness = max(
        0.0,
        observed_at.timestamp() - (float(latest["ts"]) / 1000 + 300),
    )
    regular_open = (
        now_local.weekday() < 5
        and time(9, 30) <= now_local.time().replace(tzinfo=None) < time(16, 0)
    )
    fresh = freshness <= 20 * 60 if regular_open else True
    today_rows = [row for row in m5 if _local(row).date() == now_local.date()]
    opening = [
        row for row in today_rows
        if time(9, 30) <= _local(row).time().replace(tzinfo=None) < time(9, 45)
    ]
    range_data = None
    daily_atr = atr14(daily)
    if len(opening) == 3:
        range_high = max(float(row["high"]) for row in opening)
        range_low = min(float(row["low"]) for row in opening)
        width = range_high - range_low
        range_data = {
            "high": range_high,
            "low": range_low,
            "width": width,
            "atr_ratio": width / daily_atr if daily_atr else None,
        }

    engine = OpeningRangeEngine()
    engine.init({})
    signal = engine.on_candle(
        latest,
        StrategyContext(
            candles=m5,
            symbol="NVDA",
            timeframe="5m",
            candles_by_timeframe={"1d": daily},
        ),
    )
    signal_payload = None
    if signal is not None:
        signal_payload = {
            "side": signal.side.value,
            "reason": signal.entry_reason,
            "stop": signal.suggested_stop,
            "target": signal.suggested_take_profit,
            "metadata": signal.strategy_metadata,
        }

    return {
        "status": "CONNECTED_PAPER" if fresh else "STALE",
        "connected": True,
        "provider": SOURCE_NAME,
        "grade": SOURCE_GRADE,
        "symbol": "NVDA",
        "timeframes": {"5m": len(m5), "1d": len(daily)},
        "latest": {
            **latest,
            "timestamp": datetime.fromtimestamp(
                float(latest["ts"]) / 1000, tz=timezone.utc
            ).isoformat(),
            "new_york_time": latest_local.isoformat(),
        },
        "opening_range": range_data,
        "atr14_d1": daily_atr,
        "signal": signal_payload,
        "candles": [
            {
                **row,
                "new_york_time": _local(row).strftime("%H:%M"),
            }
            for row in today_rows[-12:]
        ],
        "fresh": fresh,
        "freshness_seconds": round(freshness, 1),
        "error": None,
        "observed_at": observed_at.isoformat(),
    }
