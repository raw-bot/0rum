"""Tests for Dukascopy public .bi5 parsing helpers."""

from datetime import UTC, datetime
import lzma
from pathlib import Path
import struct

import pandas as pd
import pytest

from src.data.dukascopy.bi5 import (
    build_dukascopy_bi5_url,
    decompress_bi5,
    iter_utc_hours,
    ohlcv_cache_path,
    parse_tick_bi5,
    raw_bi5_cache_path,
    resample_ticks_to_ohlcv,
    ticks_cache_path,
)


def test_build_dukascopy_bi5_url_uses_zero_based_month():
    """Dukascopy datafeed paths use January as month 00."""
    hour = datetime(2026, 5, 18, 9, tzinfo=UTC)

    url = build_dukascopy_bi5_url("XAU/USD", hour)

    assert url == "https://datafeed.dukascopy.com/datafeed/XAUUSD/2026/04/18/09h_ticks.bi5"


def test_decompress_and_parse_tick_bi5_payload():
    """Tick rows are parsed from big-endian Dukascopy binary records."""
    hour = datetime(2026, 5, 18, 9, tzinfo=UTC)
    raw_payload = b"".join(
        [
            struct.pack(">IIIff", 0, 3345123, 3344923, 1.5, 2.5),
            struct.pack(">IIIff", 900_000, 3346123, 3345923, 1.0, 2.0),
        ]
    )
    compressed = lzma.compress(raw_payload)

    df = parse_tick_bi5(decompress_bi5(compressed), hour_start=hour, symbol="XAUUSD")

    assert list(df.columns) == ["bid", "ask", "bid_volume", "ask_volume"]
    assert df.index[0] == pd.Timestamp("2026-05-18T09:00:00Z")
    assert df.index[1] == pd.Timestamp("2026-05-18T09:15:00Z")
    assert df.iloc[0]["bid"] == pytest.approx(3344.923)
    assert df.iloc[0]["ask"] == pytest.approx(3345.123)


def test_parse_tick_bi5_rejects_partial_record():
    """Corrupt binary payloads fail before producing bad candles."""
    with pytest.raises(ValueError, match="not divisible"):
        parse_tick_bi5(b"partial", hour_start=datetime(2026, 5, 18, tzinfo=UTC))


def test_resample_ticks_to_m15_ohlcv():
    """Ticks can be aggregated to 0rum research OHLCV candles."""
    index = pd.to_datetime(
        [
            "2026-05-18T09:00:00Z",
            "2026-05-18T09:01:00Z",
            "2026-05-18T09:15:00Z",
        ],
        utc=True,
    )
    ticks = pd.DataFrame(
        {
            "bid": [100.0, 102.0, 110.0],
            "ask": [101.0, 103.0, 111.0],
            "bid_volume": [1.0, 2.0, 3.0],
            "ask_volume": [4.0, 5.0, 6.0],
        },
        index=index,
    )

    candles = resample_ticks_to_ohlcv(ticks, "M15")

    assert len(candles) == 2
    assert candles.iloc[0]["open"] == pytest.approx(100.5)
    assert candles.iloc[0]["high"] == pytest.approx(102.5)
    assert candles.iloc[0]["low"] == pytest.approx(100.5)
    assert candles.iloc[0]["close"] == pytest.approx(102.5)
    assert candles.iloc[0]["volume"] == pytest.approx(12.0)


def test_iter_utc_hours_returns_half_open_range():
    """Range downloads include each hour overlapping [start, end)."""
    start = datetime(2026, 5, 18, 9, 30, tzinfo=UTC)
    end = datetime(2026, 5, 18, 11, 0, tzinfo=UTC)

    assert iter_utc_hours(start, end) == [
        datetime(2026, 5, 18, 9, tzinfo=UTC),
        datetime(2026, 5, 18, 10, tzinfo=UTC),
    ]


def test_public_cache_paths_match_downloader_layout():
    cache_dir = Path("/tmp/dukascopy-cache")
    start = datetime(2026, 5, 18, 9, tzinfo=UTC)
    end = datetime(2026, 5, 18, 10, tzinfo=UTC)

    assert raw_bi5_cache_path(cache_dir, "XAU/USD", start) == Path(
        "/tmp/dukascopy-cache/raw/XAUUSD/2026/05/18/09h_ticks.bi5"
    )
    assert ticks_cache_path(cache_dir, "XAU/USD", start, end) == Path(
        "/tmp/dukascopy-cache/ticks/XAUUSD/20260518T0900_20260518T1000_ticks.csv"
    )
    assert ohlcv_cache_path(cache_dir, "XAU/USD", start, end, "M15") == Path(
        "/tmp/dukascopy-cache/ohlcv/XAUUSD/20260518T0900_20260518T1000_M15.csv"
    )
