"""QA reports for cached Dukascopy research exports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

import pandas as pd

from src.data.dukascopy.bi5 import normalize_symbol


TIMEFRAME_FREQ = {
    "M15": "15min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1D",
}


class GapClassification(str, Enum):
    EXPECTED_MARKET_PAUSE = "expected_market_pause"
    WEEKEND_CLOSE = "weekend_close"
    SUSPICIOUS_GAP = "suspicious_gap"


@dataclass(frozen=True)
class DukascopyGap:
    start: datetime
    end: datetime
    missing_candles: int
    classification: GapClassification


@dataclass(frozen=True)
class DukascopyQAReport:
    symbol: str
    start: datetime
    end: datetime
    timeframe: str
    tick_rows: int
    candle_rows_by_timeframe: dict[str, int]
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    invalid_ohlc_count: int
    gaps: tuple[DukascopyGap, ...]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _cached_files(cache_dir: Path, symbol: str, kind: str, suffix: str) -> list[Path]:
    root = cache_dir / kind / normalize_symbol(symbol)
    if not root.exists():
        return []
    return sorted(path for path in root.rglob(f"*{suffix}") if path.is_file())


def _read_cached_csvs(paths: list[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in paths:
        frame = pd.read_csv(path)
        if "timestamp" not in frame.columns:
            continue
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frames.append(frame)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
    return combined.sort_values("timestamp").reset_index(drop=True)


def _filter_range(frame: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame[(frame["timestamp"] >= start) & (frame["timestamp"] < end)].reset_index(drop=True)


def _load_ticks(cache_dir: Path, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    ticks = _read_cached_csvs(_cached_files(cache_dir, symbol, "ticks", "_ticks.csv"))
    return _filter_range(ticks, start, end)


def load_cached_ohlcv(
    cache_dir: Path,
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe: str,
) -> pd.DataFrame:
    """Load UTC-filtered cached OHLCV CSV rows for one symbol and timeframe."""
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    candles = _read_cached_csvs(_cached_files(cache_dir, symbol, "ohlcv", f"_{timeframe}.csv"))
    return _filter_range(candles, start_utc, end_utc)


def _invalid_ohlc_count(candles: pd.DataFrame) -> int:
    required = {"open", "high", "low", "close"}
    if candles.empty or not required.issubset(candles.columns):
        return 0

    invalid = (
        (candles["high"] < candles["low"])
        | (candles["open"] > candles["high"])
        | (candles["open"] < candles["low"])
        | (candles["close"] > candles["high"])
        | (candles["close"] < candles["low"])
    )
    return int(invalid.sum())


def _has_weekend_timestamp(index: pd.DatetimeIndex) -> bool:
    return any(ts.weekday() >= 5 for ts in index)


def _is_weekend_close(missing: pd.DatetimeIndex) -> bool:
    if missing.empty:
        return False

    first = missing[0]
    last = missing[-1]
    return (
        _has_weekend_timestamp(missing)
        or (first.weekday() == 4 and first.hour >= 20)
        or (last.weekday() == 0 and last.hour <= 2)
    )


def _is_expected_market_pause(missing: pd.DatetimeIndex, timeframe: str) -> bool:
    if timeframe != "M15":
        return False
    if len(missing) != 4:
        return False

    first = missing[0]
    last = missing[-1]
    same_day = first.date() == last.date()
    near_session_end = 20 <= first.hour <= 23 and 20 <= last.hour <= 23
    return same_day and near_session_end and not _is_weekend_close(missing)


def _classify_gap(missing: pd.DatetimeIndex, timeframe: str) -> GapClassification:
    if _is_weekend_close(missing):
        return GapClassification.WEEKEND_CLOSE
    if _is_expected_market_pause(missing, timeframe):
        return GapClassification.EXPECTED_MARKET_PAUSE
    return GapClassification.SUSPICIOUS_GAP


def _contiguous_missing_groups(missing: pd.DatetimeIndex, freq: str) -> list[pd.DatetimeIndex]:
    if missing.empty:
        return []

    step = pd.Timedelta(freq)
    groups: list[list[pd.Timestamp]] = [[missing[0]]]
    for timestamp in missing[1:]:
        if timestamp - groups[-1][-1] == step:
            groups[-1].append(timestamp)
        else:
            groups.append([timestamp])
    return [pd.DatetimeIndex(group) for group in groups]


def _detect_gaps(
    candles: pd.DataFrame,
    start: datetime,
    end: datetime,
    timeframe: str,
) -> tuple[DukascopyGap, ...]:
    if timeframe not in TIMEFRAME_FREQ:
        raise ValueError(f"Unsupported Dukascopy QA timeframe: {timeframe}")
    if candles.empty:
        return ()

    freq = TIMEFRAME_FREQ[timeframe]
    expected = pd.date_range(start, end, freq=freq, inclusive="left")
    present = pd.DatetimeIndex(pd.to_datetime(candles["timestamp"], utc=True).drop_duplicates())
    missing = expected.difference(present)

    gaps: list[DukascopyGap] = []
    for group in _contiguous_missing_groups(missing, freq):
        gaps.append(
            DukascopyGap(
                start=group[0].to_pydatetime(),
                end=(group[-1] + pd.Timedelta(freq)).to_pydatetime(),
                missing_candles=len(group),
                classification=_classify_gap(group, timeframe),
            )
        )
    return tuple(gaps)


def build_qa_report(
    *,
    cache_dir: Path,
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe: str = "M15",
) -> DukascopyQAReport:
    """Build a QA report for cached Dukascopy tick and OHLCV exports."""
    symbol_norm = normalize_symbol(symbol)
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    if end_utc <= start_utc:
        raise ValueError("end must be after start")

    ticks = _load_ticks(cache_dir, symbol_norm, start_utc, end_utc)
    candles = load_cached_ohlcv(cache_dir, symbol_norm, start_utc, end_utc, timeframe)
    first_timestamp = None
    last_timestamp = None
    if not candles.empty:
        first_timestamp = candles["timestamp"].iloc[0].to_pydatetime()
        last_timestamp = candles["timestamp"].iloc[-1].to_pydatetime()

    return DukascopyQAReport(
        symbol=symbol_norm,
        start=start_utc,
        end=end_utc,
        timeframe=timeframe,
        tick_rows=len(ticks),
        candle_rows_by_timeframe={timeframe: len(candles)},
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        invalid_ohlc_count=_invalid_ohlc_count(candles),
        gaps=_detect_gaps(candles, start_utc, end_utc, timeframe),
    )
