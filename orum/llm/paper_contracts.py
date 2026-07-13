"""Immutable contracts for isolated leveraged LLM paper accounts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any


class PaperContractError(ValueError):
    """Raised when an experimental paper record is malformed."""


LANES = frozenset({"llm_reference", "llm_evolving"})


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PaperContractError(f"{name} must be non-empty text")
    return value.strip()


def _number(value: object, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise PaperContractError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PaperContractError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise PaperContractError(f"{name} must be a finite number")
    if minimum is not None and result < minimum:
        raise PaperContractError(f"{name} must be at least {minimum:g}")
    return result


def _optional_number(value: object, name: str, *, minimum: float | None = None) -> float | None:
    return None if value is None else _number(value, name, minimum=minimum)


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise PaperContractError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PaperContractError(f"{name} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise PaperContractError(f"{name} must be an integer")
    if result < minimum:
        raise PaperContractError(f"{name} must be at least {minimum}")
    return result


def _timestamp(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            result = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise PaperContractError(f"{name} must be an ISO-8601 timestamp") from exc
    else:
        raise PaperContractError(f"{name} must be an ISO-8601 timestamp")
    if result.tzinfo is None:
        raise PaperContractError(f"{name} must include a timezone")
    return result.astimezone(UTC)


def _optional_timestamp(value: object, name: str) -> datetime | None:
    return None if value is None else _timestamp(value, name)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _reject_unknown(value: Mapping[str, object], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PaperContractError(f"{name} has unknown fields: {', '.join(unknown)}")


@dataclass(frozen=True, slots=True)
class LlmPaperTarget:
    target_id: str
    price: float
    fraction_remaining: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_id", _text(self.target_id, "target_id"))
        object.__setattr__(self, "price", _number(self.price, "target price", minimum=0.0000001))
        fraction = _number(self.fraction_remaining, "target fraction", minimum=0.0000001)
        if fraction > 1:
            raise PaperContractError("target fraction must be at most 1")
        object.__setattr__(self, "fraction_remaining", fraction)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "LlmPaperTarget":
        if not isinstance(raw, Mapping):
            raise PaperContractError("paper target must be an object")
        _reject_unknown(raw, {"target_id", "price", "fraction_remaining"}, "paper target")
        return cls(
            target_id=raw.get("target_id"),
            price=raw.get("price"),
            fraction_remaining=raw.get("fraction_remaining"),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "target_id": self.target_id,
            "price": self.price,
            "fraction_remaining": self.fraction_remaining,
        }


@dataclass(frozen=True, slots=True)
class LlmPaperPosition:
    position_id: str
    decision_id: str
    lane: str
    symbol: str
    side: str
    qty: float
    initial_qty: float
    entry_px: float
    mark_px: float
    notional_usd: float
    initial_margin_usd: float
    requested_leverage: float
    effective_leverage: float
    entry_fee_usd: float
    liquidation_px: float
    liquidation_formula_version: str
    stop_loss: float | None
    take_profits: tuple[LlmPaperTarget, ...]
    trailing_stop_pct: float | None
    time_exit_at: datetime | None
    opened_at: datetime
    thesis: str
    invalidation: str

    def __post_init__(self) -> None:
        for name in ("position_id", "decision_id", "symbol", "liquidation_formula_version", "thesis", "invalidation"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        lane = _text(self.lane, "lane")
        if lane not in LANES:
            raise PaperContractError("lane must be llm_reference or llm_evolving")
        object.__setattr__(self, "lane", lane)
        side = _text(self.side, "side")
        if side not in {"long", "short"}:
            raise PaperContractError("side must be long or short")
        object.__setattr__(self, "side", side)
        for name in ("qty", "initial_qty", "entry_px", "mark_px", "notional_usd", "requested_leverage", "effective_leverage", "liquidation_px"):
            object.__setattr__(self, name, _number(getattr(self, name), name, minimum=0.0000001))
        if self.qty > self.initial_qty:
            raise PaperContractError("qty must not exceed initial_qty")
        object.__setattr__(self, "initial_margin_usd", _number(self.initial_margin_usd, "initial_margin_usd", minimum=0))
        object.__setattr__(self, "entry_fee_usd", _number(self.entry_fee_usd, "entry_fee_usd", minimum=0))
        object.__setattr__(self, "stop_loss", _optional_number(self.stop_loss, "stop_loss", minimum=0.0000001))
        object.__setattr__(self, "trailing_stop_pct", _optional_number(self.trailing_stop_pct, "trailing_stop_pct", minimum=0))
        targets = tuple(self.take_profits)
        if any(not isinstance(item, LlmPaperTarget) for item in targets):
            raise PaperContractError("take_profits must contain paper targets")
        ids = [item.target_id for item in targets]
        if len(ids) != len(set(ids)):
            raise PaperContractError("take_profits contains duplicate target IDs")
        if math.fsum(item.fraction_remaining for item in targets) > 1 + 1e-12:
            raise PaperContractError("take_profit fractions must sum to at most 1")
        object.__setattr__(self, "take_profits", targets)
        object.__setattr__(self, "time_exit_at", _optional_timestamp(self.time_exit_at, "time_exit_at"))
        object.__setattr__(self, "opened_at", _timestamp(self.opened_at, "opened_at"))
        if side == "long" and self.liquidation_px >= self.entry_px:
            raise PaperContractError("long liquidation price must be below entry")
        if side == "short" and self.liquidation_px <= self.entry_px:
            raise PaperContractError("short liquidation price must be above entry")

    @property
    def unrealized_pnl_usd(self) -> float:
        direction = 1 if self.side == "long" else -1
        return direction * self.qty * (self.mark_px - self.entry_px)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "LlmPaperPosition":
        if not isinstance(raw, Mapping):
            raise PaperContractError("paper position must be an object")
        _reject_unknown(raw, set(cls.__dataclass_fields__), "paper position")
        targets = raw.get("take_profits", [])
        if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence):
            raise PaperContractError("take_profits must be a list")
        values = dict(raw)
        values["take_profits"] = tuple(LlmPaperTarget.from_mapping(item) for item in targets)
        return cls(**values)

    def to_mapping(self) -> dict[str, object]:
        return {
            "position_id": self.position_id,
            "decision_id": self.decision_id,
            "lane": self.lane,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "initial_qty": self.initial_qty,
            "entry_px": self.entry_px,
            "mark_px": self.mark_px,
            "notional_usd": self.notional_usd,
            "initial_margin_usd": self.initial_margin_usd,
            "requested_leverage": self.requested_leverage,
            "effective_leverage": self.effective_leverage,
            "entry_fee_usd": self.entry_fee_usd,
            "liquidation_px": self.liquidation_px,
            "liquidation_formula_version": self.liquidation_formula_version,
            "stop_loss": self.stop_loss,
            "take_profits": [item.to_mapping() for item in self.take_profits],
            "trailing_stop_pct": self.trailing_stop_pct,
            "time_exit_at": _iso(self.time_exit_at),
            "opened_at": _iso(self.opened_at),
            "thesis": self.thesis,
            "invalidation": self.invalidation,
        }


@dataclass(frozen=True, slots=True)
class LlmPaperAccount:
    lane: str
    starting_balance_usd: float
    balance_usd: float
    positions: Mapping[str, LlmPaperPosition] = field(default_factory=dict)
    processed_decision_ids: tuple[str, ...] = ()
    last_processed_candles: Mapping[str, int] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        lane = _text(self.lane, "lane")
        if lane not in LANES:
            raise PaperContractError("lane must be llm_reference or llm_evolving")
        object.__setattr__(self, "lane", lane)
        object.__setattr__(self, "starting_balance_usd", _number(self.starting_balance_usd, "starting_balance_usd", minimum=0))
        object.__setattr__(self, "balance_usd", _number(self.balance_usd, "balance_usd", minimum=0))
        if self.schema_version != 1:
            raise PaperContractError("unsupported paper account schema_version")
        normalized_positions: dict[str, LlmPaperPosition] = {}
        for key, position in self.positions.items():
            position_key = _text(key, "position key")
            if not isinstance(position, LlmPaperPosition):
                raise PaperContractError("positions must contain paper positions")
            if position.lane != lane:
                raise PaperContractError("position lane does not match account lane")
            normalized_positions[position_key] = position
        object.__setattr__(self, "positions", MappingProxyType(normalized_positions))
        decision_ids = tuple(_text(item, "processed decision ID") for item in self.processed_decision_ids)
        if len(decision_ids) != len(set(decision_ids)):
            raise PaperContractError("processed decision IDs contain duplicates")
        object.__setattr__(self, "processed_decision_ids", decision_ids)
        candles = {
            _text(key, "processed candle key"): _integer(value, "processed candle")
            for key, value in self.last_processed_candles.items()
        }
        object.__setattr__(self, "last_processed_candles", MappingProxyType(candles))

    @property
    def equity_usd(self) -> float:
        return self.balance_usd + math.fsum(
            position.unrealized_pnl_usd for position in self.positions.values()
        )

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, object] | None,
        *,
        lane: str | None = None,
        starting_balance_usd: float | None = None,
    ) -> "LlmPaperAccount":
        if raw is None:
            if lane is None or starting_balance_usd is None:
                raise PaperContractError("fresh account requires lane and starting balance")
            return cls(
                lane=lane,
                starting_balance_usd=starting_balance_usd,
                balance_usd=starting_balance_usd,
            )
        if not isinstance(raw, Mapping):
            raise PaperContractError("paper account must be an object")
        _reject_unknown(raw, set(cls.__dataclass_fields__), "paper account")
        raw_positions = raw.get("positions", {})
        if not isinstance(raw_positions, Mapping):
            raise PaperContractError("positions must be an object")
        positions = {
            str(key): LlmPaperPosition.from_mapping(value)
            for key, value in raw_positions.items()
        }
        return cls(
            lane=raw.get("lane"),
            starting_balance_usd=raw.get("starting_balance_usd"),
            balance_usd=raw.get("balance_usd"),
            positions=positions,
            processed_decision_ids=tuple(raw.get("processed_decision_ids", [])),
            last_processed_candles=raw.get("last_processed_candles", {}),
            schema_version=raw.get("schema_version", 1),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "lane": self.lane,
            "starting_balance_usd": self.starting_balance_usd,
            "balance_usd": self.balance_usd,
            "positions": {key: value.to_mapping() for key, value in self.positions.items()},
            "processed_decision_ids": list(self.processed_decision_ids),
            "last_processed_candles": dict(self.last_processed_candles),
        }


@dataclass(frozen=True, slots=True)
class LlmPaperFill:
    fill_id: str
    operation_id: str
    decision_id: str
    position_id: str
    lane: str
    symbol: str
    action: str
    reason: str
    side: str
    qty: float
    price: float
    fee_usd: float
    realized_pnl_usd: float
    balance_after_usd: float
    candle_ts: int
    created_at: datetime

    def __post_init__(self) -> None:
        for name in ("fill_id", "operation_id", "decision_id", "position_id", "symbol", "reason"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        lane = _text(self.lane, "lane")
        if lane not in LANES:
            raise PaperContractError("invalid fill lane")
        object.__setattr__(self, "lane", lane)
        action = _text(self.action, "action")
        if action not in {"open", "add", "reduce", "close", "stop", "take_profit", "liquidation", "time_exit"}:
            raise PaperContractError("invalid fill action")
        object.__setattr__(self, "action", action)
        side = _text(self.side, "side")
        if side not in {"long", "short"}:
            raise PaperContractError("invalid fill side")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "qty", _number(self.qty, "qty", minimum=0.0000001))
        object.__setattr__(self, "price", _number(self.price, "price", minimum=0.0000001))
        object.__setattr__(self, "fee_usd", _number(self.fee_usd, "fee_usd", minimum=0))
        object.__setattr__(self, "realized_pnl_usd", _number(self.realized_pnl_usd, "realized_pnl_usd"))
        object.__setattr__(self, "balance_after_usd", _number(self.balance_after_usd, "balance_after_usd", minimum=0))
        object.__setattr__(self, "candle_ts", _integer(self.candle_ts, "candle_ts"))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "LlmPaperFill":
        if not isinstance(raw, Mapping):
            raise PaperContractError("paper fill must be an object")
        _reject_unknown(raw, set(cls.__dataclass_fields__), "paper fill")
        return cls(**dict(raw))

    def to_mapping(self) -> dict[str, object]:
        return {
            "fill_id": self.fill_id,
            "operation_id": self.operation_id,
            "decision_id": self.decision_id,
            "position_id": self.position_id,
            "lane": self.lane,
            "symbol": self.symbol,
            "action": self.action,
            "reason": self.reason,
            "side": self.side,
            "qty": self.qty,
            "price": self.price,
            "fee_usd": self.fee_usd,
            "realized_pnl_usd": self.realized_pnl_usd,
            "balance_after_usd": self.balance_after_usd,
            "candle_ts": self.candle_ts,
            "created_at": _iso(self.created_at),
        }
