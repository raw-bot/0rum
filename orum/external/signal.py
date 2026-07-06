"""External signal type + strict parser.

A TradingView alert (or any external source) arrives as a JSON payload. Before
0rum considers acting on it, the payload must be parsed into a well-typed,
immutable `ExternalSignal`. Parsing is STRUCTURAL only: it proves the payload
is shaped correctly, not that the signal is acceptable (that is Phase 3).

Two house rules borrowed from dsl/schema.py:
  1. Collect EVERY rejection reason, then raise once. A malformed payload
     surfaces all its problems at once, not one per round-trip.
  2. No silent fallback. A missing or wrong-typed field is a hard rejection,
     never a papered-over default — the same discipline the indicators follow.

`bar_time` is canonicalized to MILLISECONDS to match the rest of the engine
(`loop.py` stores candle ts in ms and divides deltas by 60000). A value that
looks like seconds is rejected here so the unit mismatch can never reach the
dedup or bar-close logic downstream.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class ExternalSignalSource(str, Enum):
    """Whitelisted origins. Anything outside this enum is rejected."""

    TRADINGVIEW = "tradingview"
    # The bot's own in-process AK MACD brain (orum.external.ak_macd).
    # Same payload contract as TradingView; only the producer differs.
    LOCAL = "local"


class ExternalSignalEvent(str, Enum):
    """The candidate event an external source can emit."""

    BUY_CANDIDATE = "BUY_CANDIDATE"
    SELL_CANDIDATE = "SELL_CANDIDATE"
    EXIT = "EXIT"


class ExternalSignalStatus(str, Enum):
    """Lifecycle of an external signal once 0rum owns it.

    Tracked when the signal is persisted/validated (Phase 2/3), not carried by
    the parsed payload itself — parsing only ever yields RECEIVED-grade data.
    """

    RECEIVED = "received"
    VALIDATED = "validated"
    ACCEPTED = "accepted"
    EXECUTED = "executed"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


# Required keys in every payload. `version` is recommended but optional.
REQUIRED_FIELDS = ("source", "strategy", "symbol", "timeframe", "event", "bar_time", "price")

# Plain string fields that must be present and non-empty.
_NON_EMPTY_STR_FIELDS = ("strategy", "symbol", "timeframe")

# Smallest plausible millisecond epoch (~2001-09). Any 2020s timestamp in
# SECONDS is ~1.6e9 and falls below this, so passing seconds is caught as a
# unit error instead of silently meaning "1970".
MIN_BAR_TIME_MS = 1_000_000_000_000

_SOURCE_VALUES = {member.value for member in ExternalSignalSource}
_EVENT_VALUES = {member.value for member in ExternalSignalEvent}


class ExternalSignalError(ValueError):
    """Carries every rejection reason so the ingestion log shows them all."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class ExternalSignal:
    """A parsed, well-typed external candidate signal.

    Immutable: once parsed it is a faithful record of what arrived. `raw` keeps
    the original payload verbatim for audit; `received_at` is when 0rum saw
    it (not the bar time).
    """

    source: str
    strategy: str
    symbol: str
    timeframe: str
    event: str
    bar_time: int  # milliseconds, UTC epoch
    price: float
    version: str | None
    received_at: str
    raw: dict = field(default_factory=dict)

    def dedup_key(self) -> str:
        """Composite identity. Distinct from the native `signal_id`: an
        external signal is the same event iff every one of these matches."""
        return f"{self.source}|{self.strategy}|{self.symbol}|{self.timeframe}|{self.event}|{self.bar_time}"

    def dedup_hash(self) -> str:
        return hashlib.sha256(self.dedup_key().encode()).hexdigest()[:16]

    def to_record(self) -> dict:
        """JSONL-serializable record (stable key order for diff-friendly logs)."""
        return {
            "source": self.source,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "event": self.event,
            "bar_time": self.bar_time,
            "price": self.price,
            "version": self.version,
            "received_at": self.received_at,
            "dedup_hash": self.dedup_hash(),
            "raw": self.raw,
        }


def _coerce_int(value: object) -> int | None:
    """Return an int for an int or integral float; None otherwise.

    bool is an int subclass in Python, so it is rejected explicitly — a `True`
    bar_time must never be read as 1.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def parse_external_signal(payload: dict | str, *, now: datetime | None = None) -> ExternalSignal:
    """Parse and structurally validate an external payload.

    Accepts a dict or a JSON string. Raises ExternalSignalError with ALL
    reasons on any structural problem. Does NOT apply business rules
    (allowlist, risk, dedup) — that is the validation layer (Phase 3).
    """
    errors: list[str] = []

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            raise ExternalSignalError(["payload is not valid JSON"])
    if not isinstance(payload, dict):
        raise ExternalSignalError([f"payload must be a JSON object, got {type(payload).__name__}"])

    for name in REQUIRED_FIELDS:
        if name not in payload:
            errors.append(f"missing required field {name!r}")

    # String fields ---------------------------------------------------------
    source = payload.get("source")
    if "source" in payload:
        if not isinstance(source, str):
            errors.append("source must be a string")
        elif source not in _SOURCE_VALUES:
            errors.append(f"source {source!r} not in {sorted(_SOURCE_VALUES)}")

    event = payload.get("event")
    if "event" in payload:
        if not isinstance(event, str):
            errors.append("event must be a string")
        elif event not in _EVENT_VALUES:
            errors.append(f"event {event!r} not in {sorted(_EVENT_VALUES)}")

    for name in _NON_EMPTY_STR_FIELDS:
        if name in payload:
            value = payload.get(name)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{name} must be a non-empty string")

    # bar_time (milliseconds) ----------------------------------------------
    bar_time_int: int | None = None
    if "bar_time" in payload:
        bar_time_int = _coerce_int(payload.get("bar_time"))
        if bar_time_int is None:
            errors.append("bar_time must be an integer (epoch milliseconds)")
        elif bar_time_int < MIN_BAR_TIME_MS:
            errors.append(
                f"bar_time {bar_time_int} is too small to be milliseconds "
                f"(looks like seconds); canonicalize to ms"
            )

    # price -----------------------------------------------------------------
    price = payload.get("price")
    if "price" in payload:
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            errors.append("price must be a number")
        elif not price > 0:
            errors.append(f"price must be positive, got {price}")

    # version (optional) ----------------------------------------------------
    version = payload.get("version")
    if version is not None and not isinstance(version, str):
        errors.append("version must be a string when present")

    if errors:
        raise ExternalSignalError(errors)

    received_at = (now or datetime.now(UTC)).isoformat()
    return ExternalSignal(
        source=source,
        strategy=payload["strategy"],
        symbol=payload["symbol"],
        timeframe=payload["timeframe"],
        event=event,
        bar_time=bar_time_int,  # validated above
        price=float(price),
        version=version,
        received_at=received_at,
        raw=dict(payload),
    )
