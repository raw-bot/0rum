"""Immutable, JSON-safe contracts for observable LLM trading decisions."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any


class ContractError(ValueError):
    """Raised when a model-facing contract is incomplete or inconsistent."""


def _require_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{name} must be an object")
    return value


def _reject_unknown(value: Mapping[str, object], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ContractError(f"{name} has unknown fields: {', '.join(unknown)}")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be non-empty text")
    return value.strip()


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _text(value, name)


def _number(value: object, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ContractError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ContractError(f"{name} must be a finite number")
    if minimum is not None and result < minimum:
        raise ContractError(f"{name} must be at least {minimum:g}")
    return result


def _optional_number(value: object, name: str, *, minimum: float | None = None) -> float | None:
    if value is None:
        return None
    return _number(value, name, minimum=minimum)


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ContractError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{name} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ContractError(f"{name} must be an integer")
    if minimum is not None and result < minimum:
        raise ContractError(f"{name} must be at least {minimum}")
    return result


def _optional_integer(value: object, name: str, *, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    return _integer(value, name, minimum=minimum)


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError(f"{name} must be an ISO-8601 timestamp")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        result = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ContractError(f"{name} must be an ISO-8601 timestamp") from exc
    if result.tzinfo is None:
        raise ContractError(f"{name} must include a timezone")
    return result.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _text_tuple(value: object, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ContractError(f"{name} must be a list of text values")
    result = tuple(_text(item, f"{name} item") for item in value)
    if not allow_empty and not result:
        raise ContractError(f"{name} must not be empty")
    if len(set(result)) != len(result):
        raise ContractError(f"{name} must not contain duplicates")
    return result


def _json_mapping(value: object, name: str) -> Mapping[str, Any]:
    mapping = _require_mapping(value, name)
    try:
        normalized = json.loads(json.dumps(mapping, allow_nan=False, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{name} must contain JSON-safe finite values") from exc
    return MappingProxyType(normalized)


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    kind: str
    source: str
    observed_at: datetime
    published_at: datetime | None
    title: str | None
    url: str | None
    payload: Mapping[str, Any]
    untrusted_text: bool

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "Evidence":
        value = _require_mapping(raw, "evidence")
        _reject_unknown(value, set(cls.__dataclass_fields__), "evidence")
        published = value.get("published_at")
        untrusted = value.get("untrusted_text", False)
        if not isinstance(untrusted, bool):
            raise ContractError("untrusted_text must be boolean")
        return cls(
            evidence_id=_text(value.get("evidence_id"), "evidence_id"),
            kind=_text(value.get("kind"), "kind"),
            source=_text(value.get("source"), "source"),
            observed_at=_timestamp(value.get("observed_at"), "observed_at"),
            published_at=None if published is None else _timestamp(published, "published_at"),
            title=_optional_text(value.get("title"), "title"),
            url=_optional_text(value.get("url"), "url"),
            payload=_json_mapping(value.get("payload", {}), "payload"),
            untrusted_text=untrusted,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "source": self.source,
            "observed_at": _iso(self.observed_at),
            "published_at": None if self.published_at is None else _iso(self.published_at),
            "title": self.title,
            "url": self.url,
            "payload": dict(self.payload),
            "untrusted_text": self.untrusted_text,
        }


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    snapshot_id: str
    created_at: datetime
    cutoff: datetime
    symbol: str
    candles: Mapping[str, Any]
    indicators: Mapping[str, Any]
    derivatives: Mapping[str, Any] | None
    macro: Mapping[str, Any]
    onchain: Mapping[str, Any]
    evidence: tuple[Evidence, ...]
    paper_account: Mapping[str, Any]
    content_hash: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": _iso(self.created_at),
            "cutoff": _iso(self.cutoff),
            "symbol": self.symbol,
            "candles": dict(self.candles),
            "indicators": dict(self.indicators),
            "derivatives": None if self.derivatives is None else dict(self.derivatives),
            "macro": dict(self.macro),
            "onchain": dict(self.onchain),
            "evidence": [item.to_mapping() for item in self.evidence],
            "paper_account": dict(self.paper_account),
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class MarketBrief:
    brief_id: str
    created_at: datetime
    snapshot_id: str
    bias: str
    regime: str
    horizons: tuple[str, ...]
    facts: tuple[str, ...]
    evidence_completeness: float
    evidence_freshness: str
    narrative_vs_price: str
    interpretation: str
    pain_trade: str
    main_scenario: str
    alternate_scenarios: tuple[str, ...]
    catalysts: tuple[str, ...]
    confidence: float
    invalidation: str
    memo_fr: str
    evidence_ids: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "MarketBrief":
        value = _require_mapping(raw, "market_brief")
        _reject_unknown(value, set(cls.__dataclass_fields__), "market_brief")
        bias = _text(value.get("bias"), "bias")
        if bias not in {"bullish", "bearish", "neutral", "uncertain"}:
            raise ContractError("bias must be bullish, bearish, neutral, or uncertain")
        completeness = _number(value.get("evidence_completeness"), "evidence_completeness")
        confidence = _number(value.get("confidence"), "confidence")
        if not 0 <= completeness <= 1:
            raise ContractError("evidence_completeness must be between 0 and 1")
        if not 0 <= confidence <= 1:
            raise ContractError("confidence must be between 0 and 1")
        return cls(
            brief_id=_text(value.get("brief_id"), "brief_id"),
            created_at=_timestamp(value.get("created_at"), "created_at"),
            snapshot_id=_text(value.get("snapshot_id"), "snapshot_id"),
            bias=bias,
            regime=_text(value.get("regime"), "regime"),
            horizons=_text_tuple(value.get("horizons"), "horizons"),
            facts=_text_tuple(value.get("facts"), "facts"),
            evidence_completeness=completeness,
            evidence_freshness=_text(value.get("evidence_freshness"), "evidence_freshness"),
            narrative_vs_price=_text(value.get("narrative_vs_price"), "narrative_vs_price"),
            interpretation=_text(value.get("interpretation"), "interpretation"),
            pain_trade=_text(value.get("pain_trade"), "pain_trade"),
            main_scenario=_text(value.get("main_scenario"), "main_scenario"),
            alternate_scenarios=_text_tuple(value.get("alternate_scenarios"), "alternate_scenarios"),
            catalysts=_text_tuple(value.get("catalysts", []), "catalysts", allow_empty=True),
            confidence=confidence,
            invalidation=_text(value.get("invalidation"), "invalidation"),
            memo_fr=_text(value.get("memo_fr"), "memo_fr"),
            evidence_ids=_text_tuple(value.get("evidence_ids", []), "evidence_ids", allow_empty=True),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "brief_id": self.brief_id,
            "created_at": _iso(self.created_at),
            "snapshot_id": self.snapshot_id,
            "bias": self.bias,
            "regime": self.regime,
            "horizons": list(self.horizons),
            "facts": list(self.facts),
            "evidence_completeness": self.evidence_completeness,
            "evidence_freshness": self.evidence_freshness,
            "narrative_vs_price": self.narrative_vs_price,
            "interpretation": self.interpretation,
            "pain_trade": self.pain_trade,
            "main_scenario": self.main_scenario,
            "alternate_scenarios": list(self.alternate_scenarios),
            "catalysts": list(self.catalysts),
            "confidence": self.confidence,
            "invalidation": self.invalidation,
            "memo_fr": self.memo_fr,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class TakeProfit:
    price: float
    fraction: float

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "TakeProfit":
        value = _require_mapping(raw, "take_profit")
        _reject_unknown(value, {"price", "fraction"}, "take_profit")
        price = _number(value.get("price"), "take_profit price", minimum=0)
        fraction = _number(value.get("fraction"), "take_profit fraction", minimum=0)
        if price == 0:
            raise ContractError("take_profit price must be positive")
        if not 0 < fraction <= 1:
            raise ContractError("take_profit fraction must be between 0 and 1")
        return cls(price=price, fraction=fraction)

    def to_mapping(self) -> dict[str, float]:
        return {"price": self.price, "fraction": self.fraction}


@dataclass(frozen=True, slots=True)
class ProposedDecision:
    decision_id: str
    created_at: datetime
    lane: str
    symbol: str
    horizon: str
    action: str
    equity_fraction: float
    requested_leverage: float
    order_type: str
    limit_price: float | None
    stop_loss: float | None
    take_profits: tuple[TakeProfit, ...]
    trailing_stop_pct: float | None
    time_exit_minutes: int | None
    confidence: float
    thesis: str
    counter_thesis: str
    risk_rationale: str
    invalidation: str
    memo_fr: str
    evidence_ids: tuple[str, ...]
    lesson_ids: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "ProposedDecision":
        value = _require_mapping(raw, "decision")
        _reject_unknown(value, set(cls.__dataclass_fields__), "decision")
        action = _text(value.get("action"), "action")
        allowed_actions = {"hold", "open_long", "open_short", "add", "reduce", "close"}
        if action not in allowed_actions:
            raise ContractError(f"action must be one of {', '.join(sorted(allowed_actions))}")
        lane = _text(value.get("lane"), "lane")
        if lane not in {"llm_reference", "llm_evolving"}:
            raise ContractError("lane must be llm_reference or llm_evolving")
        order_type = _text(value.get("order_type"), "order_type")
        if order_type not in {"market", "limit"}:
            raise ContractError("order_type must be market or limit")
        limit_price = _optional_number(value.get("limit_price"), "limit_price", minimum=0)

        equity_fraction = _number(value.get("equity_fraction"), "equity_fraction", minimum=0)
        requested_leverage = _number(value.get("requested_leverage"), "requested_leverage", minimum=0)
        confidence = _number(value.get("confidence"), "confidence")
        if equity_fraction > 1:
            raise ContractError("equity_fraction must be between 0 and 1")
        if confidence < 0 or confidence > 1:
            raise ContractError("confidence must be between 0 and 1")

        raw_targets = value.get("take_profits", [])
        if isinstance(raw_targets, (str, bytes)) or not isinstance(raw_targets, Sequence):
            raise ContractError("take_profits must be a list")
        targets = tuple(TakeProfit.from_mapping(item) for item in raw_targets)
        if math.fsum(item.fraction for item in targets) > 1 + 1e-12:
            raise ContractError("take_profits fractions must sum to at most 1")
        stop_loss = _optional_number(value.get("stop_loss"), "stop_loss", minimum=0)
        trailing_stop = _optional_number(value.get("trailing_stop_pct"), "trailing_stop_pct", minimum=0)
        if trailing_stop is not None and not 0 < trailing_stop < 1:
            raise ContractError("trailing_stop_pct must be a decimal fraction between 0 and 1")
        time_exit = _optional_integer(value.get("time_exit_minutes"), "time_exit_minutes", minimum=1)

        if action == "hold":
            if equity_fraction != 0 or requested_leverage != 0 or stop_loss is not None or targets:
                raise ContractError("hold must not contain size, leverage, stop_loss, or take_profits")
            # A hold does not place an order.  Models sometimes describe a
            # future limit idea in the memo and leak `order_type=limit` into
            # the structured decision; canonicalize that inert metadata rather
            # than discarding a valid no-trade decision.
            order_type = "market"
            limit_price = None
        elif action in {"open_long", "open_short", "add"}:
            if equity_fraction <= 0:
                raise ContractError("equity_fraction must be positive for an entry")
            if requested_leverage <= 0:
                raise ContractError("requested_leverage must be positive for an entry")
            if stop_loss is None or stop_loss == 0 or not targets:
                raise ContractError("entry requires stop_loss and take_profits")
        if action != "hold" and order_type == "limit" and (limit_price is None or limit_price == 0):
            raise ContractError("limit_price is required for a limit order")
        if action == "open_long" and any(stop_loss >= target.price for target in targets):
            raise ContractError("open_long geometry requires stop_loss below every target")
        if action == "open_short" and any(stop_loss <= target.price for target in targets):
            raise ContractError("open_short geometry requires stop_loss above every target")

        return cls(
            decision_id=_text(value.get("decision_id"), "decision_id"),
            created_at=_timestamp(value.get("created_at"), "created_at"),
            lane=lane,
            symbol=_text(value.get("symbol"), "symbol"),
            horizon=_text(value.get("horizon"), "horizon"),
            action=action,
            equity_fraction=equity_fraction,
            requested_leverage=requested_leverage,
            order_type=order_type,
            limit_price=limit_price,
            stop_loss=stop_loss,
            take_profits=targets,
            trailing_stop_pct=trailing_stop,
            time_exit_minutes=time_exit,
            confidence=confidence,
            thesis=_text(value.get("thesis"), "thesis"),
            counter_thesis=_text(value.get("counter_thesis"), "counter_thesis"),
            risk_rationale=_text(value.get("risk_rationale"), "risk_rationale"),
            invalidation=_text(value.get("invalidation"), "invalidation"),
            memo_fr=_text(value.get("memo_fr"), "memo_fr"),
            evidence_ids=_text_tuple(value.get("evidence_ids", []), "evidence_ids", allow_empty=True),
            lesson_ids=_text_tuple(value.get("lesson_ids", []), "lesson_ids", allow_empty=True),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "created_at": _iso(self.created_at),
            "lane": self.lane,
            "symbol": self.symbol,
            "horizon": self.horizon,
            "action": self.action,
            "equity_fraction": self.equity_fraction,
            "requested_leverage": self.requested_leverage,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "stop_loss": self.stop_loss,
            "take_profits": [item.to_mapping() for item in self.take_profits],
            "trailing_stop_pct": self.trailing_stop_pct,
            "time_exit_minutes": self.time_exit_minutes,
            "confidence": self.confidence,
            "thesis": self.thesis,
            "counter_thesis": self.counter_thesis,
            "risk_rationale": self.risk_rationale,
            "invalidation": self.invalidation,
            "memo_fr": self.memo_fr,
            "evidence_ids": list(self.evidence_ids),
            "lesson_ids": list(self.lesson_ids),
        }
