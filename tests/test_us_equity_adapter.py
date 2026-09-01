from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pandas as pd
import pytest

from orum.adapters import us_equity

NEW_YORK = ZoneInfo("America/New_York")


def _frame(index, rows):
    columns = pd.MultiIndex.from_product(
        [["Open", "High", "Low", "Close", "Volume"], ["NVDA"]]
    )
    return pd.DataFrame(rows, index=index, columns=columns)


def test_m5_provider_keeps_only_closed_regular_session_bars():
    index = pd.DatetimeIndex([
        datetime(2026, 8, 3, 9, 30, tzinfo=NEW_YORK),
        datetime(2026, 8, 3, 9, 35, tzinfo=NEW_YORK),
        datetime(2026, 8, 3, 9, 40, tzinfo=NEW_YORK),
    ])
    frame = _frame(index, [
        [100, 101, 99, 100, 1000],
        [100, 102, 100, 101, 1100],
        [101, 102, 100, 101, 1200],
    ])
    now = datetime(2026, 8, 3, 9, 43, tzinfo=NEW_YORK)
    us_equity.clear_us_equity_cache()

    with patch.object(us_equity, "_download", return_value=frame):
        candles = us_equity.fetch_us_equity_candles("NVDA", "5m", 10, now=now)

    assert len(candles) == 2
    assert candles[-1]["close"] == 101.0
    assert candles == sorted(candles, key=lambda row: row["ts"])


def test_daily_provider_drops_todays_forming_bar():
    index = pd.DatetimeIndex([
        datetime(2026, 7, 31),
        datetime(2026, 8, 3),
    ])
    frame = _frame(index, [
        [100, 101, 99, 100, 1000],
        [101, 102, 100, 101, 1100],
    ])
    now = datetime(2026, 8, 3, 13, 0, tzinfo=NEW_YORK)
    us_equity.clear_us_equity_cache()

    with patch.object(us_equity, "_download", return_value=frame):
        candles = us_equity.fetch_us_equity_candles("NVDA", "1d", 10, now=now)

    assert len(candles) == 1
    assert candles[0]["close"] == 100.0


def test_invalid_ohlc_is_rejected_instead_of_reaching_strategy():
    index = pd.DatetimeIndex([
        datetime(2026, 8, 3, 9, 30, tzinfo=NEW_YORK),
    ])
    frame = _frame(index, [[100, 99, 98, 100, 1000]])
    now = datetime(2026, 8, 3, 10, 0, tzinfo=NEW_YORK)
    us_equity.clear_us_equity_cache()

    with patch.object(us_equity, "_download", return_value=frame):
        with pytest.raises(us_equity.UsEquityDataError, match="OHLCV bounds"):
            us_equity.fetch_us_equity_candles("NVDA", "5m", 10, now=now)


def test_dashboard_can_discard_one_latest_daily_row_with_missing_close():
    index = pd.DatetimeIndex([
        datetime(2026, 7, 31),
        datetime(2026, 8, 3),
    ])
    frame = _frame(index, [
        [198.44, 202.0, 194.95, 200.75, 139_659_700],
        [197.73, 208.74, 196.85, float("nan"), 127_547_828],
    ])
    now = datetime(2026, 8, 4, 7, 0, tzinfo=NEW_YORK)
    diagnostics = []
    us_equity.clear_us_equity_cache()

    with patch.object(us_equity, "_download", return_value=frame):
        with pytest.raises(us_equity.UsEquityDataError, match="non-finite OHLCV"):
            us_equity.fetch_us_equity_candles("NVDA", "1d", 10, now=now)
        candles = us_equity.fetch_us_equity_candles(
            "NVDA",
            "1d",
            10,
            now=now,
            allow_trailing_daily_missing_close=True,
            diagnostics=diagnostics,
        )

    assert [row["close"] for row in candles] == [200.75]
    assert diagnostics == [
        "bougie D1 invalide du 2026-08-03 écartée : clôture Yahoo non valide"
    ]


def test_dashboard_still_rejects_non_finite_non_latest_daily_row():
    index = pd.DatetimeIndex([
        datetime(2026, 7, 31),
        datetime(2026, 8, 3),
    ])
    frame = _frame(index, [
        [198.44, 202.0, 194.95, float("nan"), 139_659_700],
        [197.73, 208.74, 196.85, 206.69, 127_547_828],
    ])
    now = datetime(2026, 8, 4, 7, 0, tzinfo=NEW_YORK)
    us_equity.clear_us_equity_cache()

    with patch.object(us_equity, "_download", return_value=frame):
        with pytest.raises(us_equity.UsEquityDataError, match="non-finite OHLCV"):
            us_equity.fetch_us_equity_candles(
                "NVDA",
                "1d",
                10,
                now=now,
                allow_trailing_daily_missing_close=True,
            )
