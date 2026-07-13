"""Canonical point-in-time market snapshots with stable content hashes."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

from orum.llm.contracts import Evidence, MarketSnapshot


class SnapshotError(ValueError):
    """Raised when a snapshot could contain malformed or forward-looking data."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise SnapshotError(f"{name} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise SnapshotError(f"{name} must be finite")
    return number


def _json_object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SnapshotError(f"{name} must be an object")
    try:
        normalized = json.loads(
            json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True)
        )
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f"{name} must contain JSON-safe finite values") from exc
    return normalized


def _timeframe_delta(timeframe: str) -> timedelta:
    match = re.fullmatch(r"([1-9]\d*)([mhd])", timeframe)
    if match is None:
        raise SnapshotError(f"unsupported timeframe {timeframe!r}")
    amount = int(match.group(1))
    unit = match.group(2)
    return {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]


def _normalize_candles(
    candles: Mapping[str, Sequence[Mapping[str, object]]],
    cutoff: datetime,
) -> dict[str, list[dict[str, float | int]]]:
    if not isinstance(candles, Mapping) or not candles:
        raise SnapshotError("candles must contain at least one timeframe")
    normalized: dict[str, list[dict[str, float | int]]] = {}
    for raw_timeframe, raw_rows in candles.items():
        if not isinstance(raw_timeframe, str):
            raise SnapshotError("candle timeframe must be text")
        timeframe = raw_timeframe.strip()
        interval = _timeframe_delta(timeframe)
        if isinstance(raw_rows, (str, bytes)) or not isinstance(raw_rows, Sequence):
            raise SnapshotError(f"candles[{timeframe}] must be a list")
        rows: list[dict[str, float | int]] = []
        seen_timestamps: set[int] = set()
        for raw in raw_rows:
            if not isinstance(raw, Mapping):
                raise SnapshotError(f"candles[{timeframe}] contains a non-object")
            timestamp_value = raw.get("ts")
            if isinstance(timestamp_value, bool):
                raise SnapshotError(f"candles[{timeframe}].ts must be epoch milliseconds")
            try:
                timestamp = int(timestamp_value)
            except (TypeError, ValueError) as exc:
                raise SnapshotError(f"candles[{timeframe}].ts must be epoch milliseconds") from exc
            if timestamp < 0 or (isinstance(timestamp_value, float) and not timestamp_value.is_integer()):
                raise SnapshotError(f"candles[{timeframe}].ts must be epoch milliseconds")
            if timestamp in seen_timestamps:
                raise SnapshotError(f"duplicate candle timestamp in {timeframe}: {timestamp}")
            seen_timestamps.add(timestamp)
            opened_at = datetime.fromtimestamp(timestamp / 1000, tz=UTC)
            if opened_at + interval > cutoff:
                raise SnapshotError(
                    f"candle {timeframe}@{timestamp} is not closed at cutoff {cutoff.isoformat()}"
                )
            open_price = _finite(raw.get("open"), f"candles[{timeframe}].open")
            high = _finite(raw.get("high"), f"candles[{timeframe}].high")
            low = _finite(raw.get("low"), f"candles[{timeframe}].low")
            close = _finite(raw.get("close"), f"candles[{timeframe}].close")
            volume = _finite(raw.get("volume"), f"candles[{timeframe}].volume")
            if min(open_price, high, low, close) <= 0:
                raise SnapshotError(f"candles[{timeframe}] prices must be positive")
            if high < max(open_price, close, low) or low > min(open_price, close, high):
                raise SnapshotError(f"candles[{timeframe}] has invalid OHLC geometry")
            if volume < 0:
                raise SnapshotError(f"candles[{timeframe}].volume must be non-negative")
            rows.append(
                {
                    "ts": timestamp,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }
            )
        rows.sort(key=lambda row: row["ts"])
        normalized[timeframe] = rows
    return normalized


class MarketSnapshotBuilder:
    def __init__(self, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._clock = clock

    def build(
        self,
        *,
        cutoff: datetime,
        symbol: str,
        candles: Mapping[str, Sequence[Mapping[str, object]]],
        indicators: Mapping[str, object],
        derivatives: Mapping[str, object] | object | None,
        macro: Mapping[str, object],
        onchain: Mapping[str, object],
        evidence: Sequence[Evidence],
        paper_account: Mapping[str, object],
    ) -> MarketSnapshot:
        if cutoff.tzinfo is None:
            raise SnapshotError("cutoff must include a timezone")
        cutoff_utc = cutoff.astimezone(UTC)
        if not isinstance(symbol, str) or not symbol.strip():
            raise SnapshotError("symbol must be non-empty")
        created_at = self._clock()
        if created_at.tzinfo is None:
            raise SnapshotError("snapshot clock must include a timezone")
        created_at = created_at.astimezone(UTC)

        normalized_candles = _normalize_candles(candles, cutoff_utc)
        normalized_evidence = self._normalize_evidence(evidence, cutoff_utc)
        derivatives_mapping = self._derivatives_mapping(derivatives)
        hash_payload = {
            "cutoff": cutoff_utc.isoformat(),
            "symbol": symbol.strip(),
            "candles": normalized_candles,
            "indicators": _json_object(indicators, "indicators"),
            "derivatives": derivatives_mapping,
            "macro": _json_object(macro, "macro"),
            "onchain": _json_object(onchain, "onchain"),
            "evidence": [item.to_mapping() for item in normalized_evidence],
            "paper_account": _json_object(paper_account, "paper_account"),
        }
        try:
            canonical = json.dumps(
                hash_payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SnapshotError("snapshot cannot be encoded as canonical JSON") from exc
        digest = hashlib.sha256(canonical).hexdigest()
        return MarketSnapshot(
            snapshot_id=f"snap-{digest[:24]}",
            created_at=created_at,
            cutoff=cutoff_utc,
            symbol=symbol.strip(),
            candles=MappingProxyType(hash_payload["candles"]),
            indicators=MappingProxyType(hash_payload["indicators"]),
            derivatives=MappingProxyType(hash_payload["derivatives"]),
            macro=MappingProxyType(hash_payload["macro"]),
            onchain=MappingProxyType(hash_payload["onchain"]),
            evidence=normalized_evidence,
            paper_account=MappingProxyType(hash_payload["paper_account"]),
            content_hash=digest,
        )

    @staticmethod
    def _normalize_evidence(
        evidence: Sequence[Evidence],
        cutoff: datetime,
    ) -> tuple[Evidence, ...]:
        if isinstance(evidence, (str, bytes)) or not isinstance(evidence, Sequence):
            raise SnapshotError("evidence must be a list")
        seen: set[str] = set()
        normalized = []
        for item in evidence:
            if not isinstance(item, Evidence):
                raise SnapshotError("evidence contains an invalid contract")
            if item.evidence_id in seen:
                raise SnapshotError(f"duplicate evidence_id {item.evidence_id}")
            seen.add(item.evidence_id)
            if item.kind == "headline" and item.published_at is None:
                raise SnapshotError(f"headline {item.evidence_id} has no publication timestamp")
            if item.published_at is not None and item.published_at > cutoff:
                raise SnapshotError(f"evidence {item.evidence_id} was published after cutoff")
            normalized.append(item)
        return tuple(
            sorted(
                normalized,
                key=lambda item: (
                    item.published_at or item.observed_at,
                    item.evidence_id,
                ),
                reverse=True,
            )
        )

    @staticmethod
    def _derivatives_mapping(value: Mapping[str, object] | object | None) -> dict[str, Any]:
        if value is None:
            return {"status": "unavailable"}
        if isinstance(value, Mapping):
            return _json_object(value, "derivatives")
        to_mapping = getattr(value, "to_mapping", None)
        if not callable(to_mapping):
            raise SnapshotError("derivatives must be a mapping or expose to_mapping()")
        return _json_object(to_mapping(), "derivatives")
