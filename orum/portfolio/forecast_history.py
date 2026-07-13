"""Append-only forecast prediction/realization audit helpers.

Pure transformations: PaperEngine owns file I/O, this module owns the stable
record contract and timestamp matching.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

AUDIT_HORIZONS = (6, 12, 24)
AUDIT_QUANTILES = ("p10", "p50", "p90")
MAX_REALIZATION_DELAY = timedelta(minutes=90)


def _datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if value is None:
        return None
    try:
        numeric = float(value)
        seconds = numeric / (1000 if numeric > 10_000_000_000 else 1)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _iso(value) -> str | None:
    parsed = _datetime(value)
    return parsed.isoformat(timespec="seconds") if parsed else None


def _bucket(origin_ts) -> str | None:
    origin = _datetime(origin_ts)
    if origin is None:
        return None
    bucket = origin.replace(hour=(origin.hour // 6) * 6, minute=0, second=0, microsecond=0)
    return bucket.isoformat(timespec="seconds")


def prediction_record(
    report: dict,
    *,
    strategy_id: str,
    symbol: str,
    decision: dict,
    recorded_at: str,
) -> dict | None:
    origin_ts = _iso(report.get("origin_ts"))
    bucket_ts = _bucket(report.get("origin_ts"))
    try:
        origin_price = float(report["origin_price"])
    except (KeyError, TypeError, ValueError):
        return None
    if origin_ts is None or bucket_ts is None or origin_price <= 0:
        return None
    horizons: dict[str, dict[str, float]] = {}
    for horizon in AUDIT_HORIZONS:
        quantiles = ((report.get("horizons") or {}).get(str(horizon)) or {}).get("quantiles") or {}
        try:
            horizons[str(horizon)] = {
                name: float(quantiles[name]) for name in AUDIT_QUANTILES
            }
        except (KeyError, TypeError, ValueError):
            return None
    return {
        "record_type": "prediction",
        "recorded_at": recorded_at,
        "strategy_id": strategy_id,
        "symbol": symbol,
        "bucket_ts": bucket_ts,
        "origin_ts": origin_ts,
        "origin_price": origin_price,
        "active": report.get("active") is True,
        "decision": dict(decision or {}),
        "lock_reasons": list(report.get("lock_reasons") or []),
        "horizons": horizons,
    }


def _prediction_key(record: dict) -> tuple[str, str]:
    return str(record.get("strategy_id")), str(record.get("bucket_ts"))


def compose_forecast_history(records: list[dict], *, strategy_id: str | None = None) -> list[dict]:
    predictions: dict[tuple[str, str], dict] = {}
    for record in records:
        if record.get("record_type") != "prediction":
            continue
        if strategy_id is not None and record.get("strategy_id") != strategy_id:
            continue
        prediction = dict(record)
        prediction["realized"] = {}
        predictions[_prediction_key(record)] = prediction
    for record in records:
        if record.get("record_type") != "realization":
            continue
        key = _prediction_key(record)
        if key not in predictions:
            continue
        horizon = str(record.get("horizon_hours"))
        predictions[key]["realized"][horizon] = {
            key: value for key, value in record.items()
            if key not in {"record_type", "strategy_id", "symbol", "bucket_ts"}
        }
    return sorted(predictions.values(), key=lambda row: row.get("origin_ts", ""))


def merge_forecast_history_24h(
    reconstructed: list[dict],
    records: list[dict],
    *,
    strategy_id: str,
    limit: int = 168,
) -> list[dict]:
    """Overlay matured live +24h audits on the honest walk-forward trace."""
    merged: dict[str, dict] = {}
    for row in reconstructed or []:
        origin_ts = _iso(row.get("origin_ts"))
        target_ts = _iso(row.get("target_ts"))
        try:
            normalized = {
                "origin_ts": origin_ts,
                "target_ts": target_ts,
                "origin_price": float(row["origin_price"]),
                "predicted_price": float(row["predicted_price"]),
                "actual_price": float(row["actual_price"]),
                "median_return": float(row["median_return"]),
                "median_error": float(row["median_error"]),
                "source": "walk_forward",
            }
        except (KeyError, TypeError, ValueError):
            continue
        if origin_ts is not None and target_ts is not None:
            merged[target_ts] = normalized

    for prediction in compose_forecast_history(records, strategy_id=strategy_id):
        realized = (prediction.get("realized") or {}).get("24")
        origin = _datetime(prediction.get("origin_ts"))
        if realized is None or origin is None:
            continue
        try:
            origin_price = float(prediction["origin_price"])
            median_return = float(prediction["horizons"]["24"]["p50"])
            actual_price = float(realized["actual_price"])
            median_error = float(realized["median_error"])
        except (KeyError, TypeError, ValueError):
            continue
        target_ts = (origin + timedelta(hours=24)).isoformat(timespec="seconds")
        merged[target_ts] = {
            "origin_ts": origin.isoformat(timespec="seconds"),
            "target_ts": target_ts,
            "origin_price": origin_price,
            "predicted_price": origin_price * (1 + median_return),
            "actual_price": actual_price,
            "median_return": median_return,
            "median_error": median_error,
            "source": "live_archive",
        }

    return [merged[key] for key in sorted(merged)][-max(0, int(limit)):]


def due_realization_records(
    records: list[dict],
    *,
    strategy_id: str,
    candles: list[dict],
    recorded_at: str,
) -> list[dict]:
    existing = {
        (str(row.get("strategy_id")), str(row.get("bucket_ts")), int(row.get("horizon_hours", 0)))
        for row in records if row.get("record_type") == "realization"
    }
    normalized_candles = []
    for candle in candles:
        candle_ts = _datetime(candle.get("ts"))
        try:
            close = float(candle.get("close"))
        except (TypeError, ValueError):
            continue
        if candle_ts is not None and close > 0:
            normalized_candles.append((candle_ts, close))
    normalized_candles.sort(key=lambda item: item[0])

    out: list[dict] = []
    for prediction in compose_forecast_history(records, strategy_id=strategy_id):
        origin = _datetime(prediction.get("origin_ts"))
        if origin is None:
            continue
        origin_price = float(prediction.get("origin_price", 0.0) or 0.0)
        if origin_price <= 0:
            continue
        for horizon in AUDIT_HORIZONS:
            key = (strategy_id, str(prediction.get("bucket_ts")), horizon)
            if key in existing:
                continue
            target = origin + timedelta(hours=horizon)
            actual = next(((stamp, close) for stamp, close in normalized_candles if stamp >= target), None)
            if actual is None:
                continue
            actual_ts, actual_price = actual
            if actual_ts - target > MAX_REALIZATION_DELAY:
                continue
            actual_return = actual_price / origin_price - 1.0
            median_return = float(prediction["horizons"][str(horizon)]["p50"])
            out.append({
                "record_type": "realization",
                "recorded_at": recorded_at,
                "strategy_id": strategy_id,
                "symbol": prediction.get("symbol"),
                "bucket_ts": prediction.get("bucket_ts"),
                "origin_ts": prediction.get("origin_ts"),
                "horizon_hours": horizon,
                "actual_ts": actual_ts.isoformat(timespec="seconds"),
                "actual_price": actual_price,
                "actual_return": actual_return,
                "median_return": median_return,
                "median_error": actual_return - median_return,
                "direction_correct": (median_return > 0) == (actual_return > 0),
            })
            existing.add(key)
    return out
