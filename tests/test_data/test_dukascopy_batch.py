"""Tests for guarded Dukascopy batch planning."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.data.dukascopy.batch import build_batch_plan
from src.data.dukascopy.bi5 import empty_bi5_cache_path, ohlcv_cache_path, raw_bi5_cache_path


def test_build_batch_plan_splits_half_open_utc_ranges(tmp_path):
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2020, 1, 11, tzinfo=UTC)

    plan = build_batch_plan(
        symbol="XAU/USD",
        start=start,
        end=end,
        batch_days=5,
        max_days=10,
        cache_dir=tmp_path,
        timeframes=("M15", "H1"),
    )

    assert plan.symbol == "XAUUSD"
    assert plan.total_days == 10.0
    assert plan.dry_run is True
    assert not hasattr(plan, "timeframes")
    assert plan.total_expected_hours == 240
    assert [batch.expected_hours for batch in plan.batches] == [120, 120]
    assert [batch.start for batch in plan.batches] == [
        datetime(2020, 1, 1, tzinfo=UTC),
        datetime(2020, 1, 6, tzinfo=UTC),
    ]
    assert [batch.end for batch in plan.batches] == [
        datetime(2020, 1, 6, tzinfo=UTC),
        datetime(2020, 1, 11, tzinfo=UTC),
    ]
    assert plan.batches[0].ohlcv_paths == {
        "M15": ohlcv_cache_path(tmp_path, "XAUUSD", plan.batches[0].start, plan.batches[0].end, "M15"),
        "H1": ohlcv_cache_path(tmp_path, "XAUUSD", plan.batches[0].start, plan.batches[0].end, "H1"),
    }


def test_build_batch_plan_counts_cached_and_missing_raw_files(tmp_path):
    start = datetime(2026, 5, 18, 9, tzinfo=UTC)
    end = datetime(2026, 5, 18, 12, tzinfo=UTC)
    raw_bi5_cache_path(tmp_path, "XAUUSD", datetime(2026, 5, 18, 9, tzinfo=UTC)).parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    raw_bi5_cache_path(tmp_path, "XAUUSD", datetime(2026, 5, 18, 9, tzinfo=UTC)).write_bytes(b"cached")
    raw_bi5_cache_path(tmp_path, "XAUUSD", datetime(2026, 5, 18, 11, tzinfo=UTC)).parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    raw_bi5_cache_path(tmp_path, "XAUUSD", datetime(2026, 5, 18, 11, tzinfo=UTC)).write_bytes(b"cached")
    ohlcv_cache_path(tmp_path, "XAUUSD", start, end, "M15").parent.mkdir(parents=True, exist_ok=True)
    ohlcv_cache_path(tmp_path, "XAUUSD", start, end, "M15").write_text("timestamp,open,high,low,close,volume\n")

    plan = build_batch_plan(
        symbol="XAUUSD",
        start=start,
        end=end,
        batch_days=1,
        max_days=1,
        cache_dir=tmp_path,
        timeframes=("M15", "H1"),
        dry_run=False,
    )

    batch = plan.batches[0]
    assert plan.dry_run is False
    assert batch.expected_hours == 3
    assert batch.cached_raw_files == 2
    assert batch.missing_raw_files == 1
    assert batch.ohlcv_paths["M15"] == ohlcv_cache_path(tmp_path, "XAUUSD", start, end, "M15")
    assert batch.ohlcv_paths["H1"] == ohlcv_cache_path(tmp_path, "XAUUSD", start, end, "H1")
    assert batch.complete_ohlcv_timeframes == ("M15",)
    assert plan.total_cached_raw_files == 2
    assert plan.total_missing_raw_files == 1


def test_build_batch_plan_counts_empty_hour_markers_as_cached(tmp_path):
    """Confirmed empty market-pause hours count as cached, not missing."""
    start = datetime(2026, 5, 18, 21, tzinfo=UTC)
    end = datetime(2026, 5, 18, 22, tzinfo=UTC)
    marker = empty_bi5_cache_path(tmp_path, "XAUUSD", start)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("404\n", encoding="ascii")

    plan = build_batch_plan(
        symbol="XAUUSD",
        start=start,
        end=end,
        batch_days=1,
        cache_dir=tmp_path,
    )

    assert plan.total_cached_raw_files == 1
    assert plan.total_missing_raw_files == 0


def test_build_batch_plan_rejects_large_range_without_max_days(tmp_path):
    with pytest.raises(RuntimeError, match="Refusing to plan 45.00 days without --max-days"):
        build_batch_plan(
            symbol="XAUUSD",
            start=datetime(2020, 1, 1, tzinfo=UTC),
            end=datetime(2020, 2, 15, tzinfo=UTC),
            batch_days=5,
            cache_dir=tmp_path,
        )


def test_build_batch_plan_rejects_range_above_max_days(tmp_path):
    with pytest.raises(RuntimeError, match="Refusing to plan 32.00 days; --max-days is 31"):
        build_batch_plan(
            symbol="XAUUSD",
            start=datetime(2020, 1, 1, tzinfo=UTC),
            end=datetime(2020, 2, 2, tzinfo=UTC),
            batch_days=5,
            max_days=31,
            cache_dir=tmp_path,
        )


def test_build_batch_plan_rejects_invalid_batch_days(tmp_path):
    with pytest.raises(ValueError, match="batch_days must be greater than 0"):
        build_batch_plan(
            symbol="XAUUSD",
            start=datetime(2020, 1, 1, tzinfo=UTC),
            end=datetime(2020, 1, 2, tzinfo=UTC),
            batch_days=0,
            max_days=1,
            cache_dir=tmp_path,
        )
