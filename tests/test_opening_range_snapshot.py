from datetime import datetime, timezone

from orum import opening_range


def _m5(hour: int, minute: int, open_px: float, high: float, low: float, close: float):
    timestamp = datetime(2026, 8, 3, hour + 4, minute, tzinfo=timezone.utc)
    return {
        "ts": int(timestamp.timestamp() * 1000),
        "open": open_px,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000.0,
    }


def _daily():
    return [
        {
            "ts": index * 86_400_000,
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 100.0,
            "volume": 1_000_000.0,
        }
        for index in range(1, 20)
    ]


def test_snapshot_exposes_connected_source_range_and_latest_price(monkeypatch):
    m5 = [
        _m5(9, 30, 100, 101, 99, 100),
        _m5(9, 35, 100, 102, 100, 101),
        _m5(9, 40, 101, 101.5, 100, 101),
        _m5(9, 45, 101, 101.5, 100, 101),
    ]
    monkeypatch.setattr(
        opening_range,
        "fetch_us_equity_candles",
        lambda _symbol, timeframe, _limit, **_kwargs: m5 if timeframe == "5m" else _daily(),
    )

    snapshot = opening_range.opening_range_snapshot(
        now=datetime(2026, 8, 3, 13, 51, tzinfo=timezone.utc)
    )

    assert snapshot["status"] == "CONNECTED_PAPER"
    assert snapshot["connected"] is True
    assert snapshot["latest"]["close"] == 101.0
    assert snapshot["opening_range"]["low"] == 99.0
    assert snapshot["opening_range"]["high"] == 102.0
    assert snapshot["timeframes"] == {"5m": 4, "1d": 19}


def test_snapshot_contains_source_failure_without_raising(monkeypatch):
    def fail(*_args, **_kwargs):
        raise opening_range.UsEquityDataError("feed unavailable")

    monkeypatch.setattr(opening_range, "fetch_us_equity_candles", fail)

    snapshot = opening_range.opening_range_snapshot()

    assert snapshot["status"] == "UNAVAILABLE"
    assert snapshot["connected"] is False
    assert snapshot["error"] == "feed unavailable"
