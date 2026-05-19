"""Tests for Dukascopy research cache QA and gap classification."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from src.data.dukascopy.bi5 import ohlcv_cache_path, ticks_cache_path
from src.data.dukascopy.qa import GapClassification, build_qa_report


def _write_ohlcv(path, timestamps):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ["timestamp,open,high,low,close,volume"]
    for index, ts in enumerate(timestamps):
        base = 2000 + index
        rows.append(f"{ts},{base},{base + 2},{base - 2},{base + 1},10")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_ticks(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "timestamp,bid,ask,bid_volume,ask_volume",
                "2020-01-02T09:00:00Z,2000,2001,1,1",
                "2020-01-02T09:00:01Z,2000.5,2001.5,1,1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_qa_report_counts_ticks_candles_and_invalid_ohlc(tmp_path):
    start = datetime(2020, 1, 2, 9, tzinfo=UTC)
    end = datetime(2020, 1, 2, 10, tzinfo=UTC)
    _write_ticks(ticks_cache_path(tmp_path, "XAUUSD", start, end))
    path = ohlcv_cache_path(tmp_path, "XAUUSD", start, end, "M15")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close,volume",
                "2020-01-02T09:00:00Z,2000,2005,1999,2001,10",
                "2020-01-02T09:15:00Z,2001,1995,1999,2002,10",
                "2020-01-02T09:30:00Z,2002,2006,2000,2003,10",
                "2020-01-02T09:45:00Z,2003,2007,2001,2004,10",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_qa_report(
        cache_dir=tmp_path,
        symbol="XAUUSD",
        start=start,
        end=end,
        timeframe="M15",
    )

    assert report.tick_rows == 2
    assert report.candle_rows_by_timeframe == {"M15": 4}
    assert report.first_timestamp == datetime(2020, 1, 2, 9, tzinfo=UTC)
    assert report.last_timestamp == datetime(2020, 1, 2, 9, 45, tzinfo=UTC)
    assert report.invalid_ohlc_count == 1
    assert report.gaps == ()


def test_qa_classifies_january_end_session_pause_without_fixed_hour(tmp_path):
    start = datetime(2020, 1, 2, 21, tzinfo=UTC)
    end = datetime(2020, 1, 2, 23, 30, tzinfo=UTC)
    expected = pd.date_range(start, end, freq="15min", inclusive="left")
    present = [ts.isoformat().replace("+00:00", "Z") for ts in expected if ts.hour != 22]
    _write_ohlcv(ohlcv_cache_path(tmp_path, "XAUUSD", start, end, "M15"), present)

    report = build_qa_report(cache_dir=tmp_path, symbol="XAUUSD", start=start, end=end, timeframe="M15")

    assert len(report.gaps) == 1
    assert report.gaps[0].missing_candles == 4
    assert report.gaps[0].classification == GapClassification.EXPECTED_MARKET_PAUSE


def test_qa_classifies_may_dst_shifted_end_session_pause(tmp_path):
    start = datetime(2026, 5, 11, 20, tzinfo=UTC)
    end = datetime(2026, 5, 11, 22, 30, tzinfo=UTC)
    expected = pd.date_range(start, end, freq="15min", inclusive="left")
    present = [ts.isoformat().replace("+00:00", "Z") for ts in expected if ts.hour != 21]
    _write_ohlcv(ohlcv_cache_path(tmp_path, "XAUUSD", start, end, "M15"), present)

    report = build_qa_report(cache_dir=tmp_path, symbol="XAUUSD", start=start, end=end, timeframe="M15")

    assert len(report.gaps) == 1
    assert report.gaps[0].missing_candles == 4
    assert report.gaps[0].classification == GapClassification.EXPECTED_MARKET_PAUSE


def test_qa_classifies_four_h1_session_end_candles_as_suspicious(tmp_path):
    start = datetime(2020, 1, 2, 18, tzinfo=UTC)
    end = datetime(2020, 1, 3, 2, tzinfo=UTC)
    present = [
        "2020-01-02T18:00:00Z",
        "2020-01-02T19:00:00Z",
        "2020-01-03T00:00:00Z",
        "2020-01-03T01:00:00Z",
    ]
    _write_ohlcv(ohlcv_cache_path(tmp_path, "XAUUSD", start, end, "H1"), present)

    report = build_qa_report(cache_dir=tmp_path, symbol="XAUUSD", start=start, end=end, timeframe="H1")

    assert len(report.gaps) == 1
    assert report.gaps[0].missing_candles == 4
    assert report.gaps[0].classification == GapClassification.SUSPICIOUS_GAP


def test_qa_classifies_weekend_close(tmp_path):
    start = datetime(2020, 1, 3, 20, tzinfo=UTC)
    end = datetime(2020, 1, 6, 2, tzinfo=UTC)
    present = [
        "2020-01-03T20:00:00Z",
        "2020-01-03T20:15:00Z",
        "2020-01-06T01:45:00Z",
    ]
    _write_ohlcv(ohlcv_cache_path(tmp_path, "XAUUSD", start, end, "M15"), present)

    report = build_qa_report(cache_dir=tmp_path, symbol="XAUUSD", start=start, end=end, timeframe="M15")

    assert report.gaps[0].classification == GapClassification.WEEKEND_CLOSE


def test_qa_classifies_mid_session_missing_candles_as_suspicious(tmp_path):
    start = datetime(2020, 1, 2, 9, tzinfo=UTC)
    end = datetime(2020, 1, 2, 11, tzinfo=UTC)
    present = [
        "2020-01-02T09:00:00Z",
        "2020-01-02T09:15:00Z",
        "2020-01-02T10:30:00Z",
        "2020-01-02T10:45:00Z",
    ]
    _write_ohlcv(ohlcv_cache_path(tmp_path, "XAUUSD", start, end, "M15"), present)

    report = build_qa_report(cache_dir=tmp_path, symbol="XAUUSD", start=start, end=end, timeframe="M15")

    assert len(report.gaps) == 1
    assert report.gaps[0].missing_candles == 4
    assert report.gaps[0].classification == GapClassification.SUSPICIOUS_GAP
