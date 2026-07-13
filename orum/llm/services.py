"""Auditable market-analyst and shadow-trader orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import json
from typing import Any

from orum.llm.contracts import ContractError, MarketBrief, MarketSnapshot, ProposedDecision
from orum.llm.journal import JsonlJournal
from orum.llm.leverage import LeverageResult, apply_leverage_policy
from orum.llm.openrouter import CompletionResult, JsonCompletionClient
from orum.llm.prompts import build_analyst_prompt, build_trader_prompt


class LlmServiceError(RuntimeError):
    """Raised after a failed model result has been made visible in its journal."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _timestamp(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None:
        raise LlmServiceError("service clock must include a timezone")
    return value.astimezone(UTC).isoformat()


def _unknown(requested: Sequence[str], allowed: set[str]) -> list[str]:
    return sorted(set(requested) - allowed)


@dataclass(frozen=True, slots=True)
class DecisionResult:
    decision: ProposedDecision
    leverage: LeverageResult | None


class MarketAnalyst:
    def __init__(
        self,
        *,
        client: JsonCompletionClient,
        journal: JsonlJournal,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.client = client
        self.journal = journal
        self._clock = clock

    def analyze(self, snapshot: MarketSnapshot) -> MarketBrief:
        prompt = build_analyst_prompt(snapshot)
        completion: CompletionResult | None = None
        raw_payload: dict[str, Any] | None = None
        try:
            completion = self.client.complete_json(
                system=prompt.system,
                user=prompt.user,
                schema=prompt.schema,
                schema_name=prompt.schema_name,
            )
            raw_payload = completion.payload
            try:
                canonical_payload = dict(raw_payload)
                canonical_payload["created_at"] = snapshot.cutoff.isoformat()
                brief = MarketBrief.from_mapping(canonical_payload)
            except ContractError as exc:
                raise LlmServiceError(f"invalid market brief: {exc}") from exc
            if brief.snapshot_id != snapshot.snapshot_id:
                raise LlmServiceError(
                    f"market brief snapshot_id mismatch: {brief.snapshot_id!r}"
                )
            unknown_evidence = _unknown(
                brief.evidence_ids,
                {item.evidence_id for item in snapshot.evidence},
            )
            if unknown_evidence:
                raise LlmServiceError(
                    f"market brief cites unknown evidence: {', '.join(unknown_evidence)}"
                )
        except Exception as exc:  # noqa: BLE001 - every model boundary failure is journaled
            error = exc if isinstance(exc, LlmServiceError) else LlmServiceError(str(exc))
            self.journal.append(
                self._record(
                    snapshot=snapshot,
                    prompt_version=prompt.version,
                    completion=completion,
                    status="model_error",
                    raw_payload=raw_payload,
                    error=error,
                )
            )
            raise error from exc

        self.journal.append(
            self._record(
                snapshot=snapshot,
                prompt_version=prompt.version,
                completion=completion,
                status="valid",
                brief=brief,
            )
        )
        return brief

    def _record(
        self,
        *,
        snapshot: MarketSnapshot,
        prompt_version: str,
        completion: CompletionResult | None,
        status: str,
        brief: MarketBrief | None = None,
        raw_payload: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "recorded_at": _timestamp(self._clock),
            "kind": "market_brief",
            "status": status,
            "model": None if completion is None else completion.model,
            "prompt_version": prompt_version,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_hash": snapshot.content_hash,
            "latency_ms": None if completion is None else completion.latency_ms,
            "usage": None if completion is None else completion.usage,
            "request_id": None if completion is None else completion.request_id,
            "brief": None if brief is None else brief.to_mapping(),
            "raw_payload": raw_payload,
            "error_type": None if error is None else type(error).__name__,
            "error": None if error is None else str(error)[:1000],
        }

class ShadowTrader:
    ENTRY_ACTIONS = frozenset({"open_long", "open_short", "add"})

    def __init__(
        self,
        *,
        client: JsonCompletionClient,
        journal: JsonlJournal,
        paper_min_leverage: float = 1.0,
        paper_max_leverage: float = 40.0,
        jurisdiction_profile: str = "fr_retail",
        asset_class: str = "crypto",
        product_kind: str = "perpetual",
        decision_timeframe: str = "15m",
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.client = client
        self.journal = journal
        self.paper_min_leverage = paper_min_leverage
        self.paper_max_leverage = paper_max_leverage
        self.jurisdiction_profile = jurisdiction_profile
        self.asset_class = asset_class
        self.product_kind = product_kind
        self.decision_timeframe = decision_timeframe
        self._clock = clock

    def decide(
        self,
        snapshot: MarketSnapshot,
        brief: MarketBrief,
        *,
        lane: str,
        lessons: Sequence[Mapping[str, Any]],
    ) -> DecisionResult:
        normalized_lessons = [dict(item) for item in lessons]
        prompt = build_trader_prompt(
            snapshot,
            brief,
            lane=lane,
            lessons=normalized_lessons,
            paper_min_leverage=self.paper_min_leverage,
            paper_max_leverage=self.paper_max_leverage,
        )
        completion: CompletionResult | None = None
        raw_payload: dict[str, Any] | None = None
        try:
            completion = self.client.complete_json(
                system=prompt.system,
                user=prompt.user,
                schema=prompt.schema,
                schema_name=prompt.schema_name,
            )
            raw_payload = completion.payload
            try:
                decision = ProposedDecision.from_mapping(raw_payload)
            except ContractError as exc:
                raise LlmServiceError(f"invalid decision: {exc}") from exc
            self._validate_provenance(
                decision=decision,
                snapshot=snapshot,
                brief=brief,
                lane=lane,
                lessons=normalized_lessons,
            )
            decision = self._canonicalize_decision(
                decision=decision,
                snapshot=snapshot,
                lane=lane,
            )
            leverage = None
            if decision.action in self.ENTRY_ACTIONS:
                leverage = apply_leverage_policy(
                    requested=decision.requested_leverage,
                    paper_min=self.paper_min_leverage,
                    paper_max=self.paper_max_leverage,
                    jurisdiction_profile=self.jurisdiction_profile,
                    asset_class=self.asset_class,
                    product_kind=self.product_kind,
                )
            result = DecisionResult(decision=decision, leverage=leverage)
        except Exception as exc:  # noqa: BLE001 - every model boundary failure is journaled
            error = exc if isinstance(exc, LlmServiceError) else LlmServiceError(str(exc))
            self.journal.append(
                self._record(
                    snapshot=snapshot,
                    brief=brief,
                    lane=lane,
                    prompt_version=prompt.version,
                    completion=completion,
                    status="model_error",
                    raw_payload=raw_payload,
                    error=error,
                )
            )
            raise error from exc

        self.journal.append(
            self._record(
                snapshot=snapshot,
                brief=brief,
                lane=lane,
                prompt_version=prompt.version,
                completion=completion,
                status="valid",
                result=result,
            )
        )
        return result

    @staticmethod
    def _canonicalize_decision(
        *, decision: ProposedDecision, snapshot: MarketSnapshot, lane: str
    ) -> ProposedDecision:
        payload = decision.to_mapping()
        payload.pop("decision_id", None)
        payload.pop("created_at", None)
        canonical = json.dumps(
            {"snapshot_id": snapshot.snapshot_id, "lane": lane, "decision": payload},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        decision_id = f"decision-{hashlib.sha256(canonical).hexdigest()[:24]}"
        return replace(
            decision,
            decision_id=decision_id,
            created_at=snapshot.cutoff,
        )

    @staticmethod
    def _validate_provenance(
        *,
        decision: ProposedDecision,
        snapshot: MarketSnapshot,
        brief: MarketBrief,
        lane: str,
        lessons: list[dict[str, Any]],
    ) -> None:
        if decision.lane != lane:
            raise LlmServiceError(
                f"decision lane mismatch: expected {lane!r}, received {decision.lane!r}"
            )
        if decision.symbol != snapshot.symbol:
            raise LlmServiceError(
                f"decision symbol mismatch: expected {snapshot.symbol!r}, received {decision.symbol!r}"
            )
        allowed_evidence = {item.evidence_id for item in snapshot.evidence}
        unknown_evidence = _unknown(decision.evidence_ids, allowed_evidence)
        if unknown_evidence:
            raise LlmServiceError(
                f"decision cites unknown evidence: {', '.join(unknown_evidence)}"
            )
        if set(decision.evidence_ids) - set(brief.evidence_ids):
            raise LlmServiceError("decision cites evidence not used by the market brief")
        allowed_lessons = {
            str(item["lesson_id"])
            for item in lessons
            if isinstance(item.get("lesson_id"), str)
        }
        unknown_lessons = _unknown(decision.lesson_ids, allowed_lessons)
        if unknown_lessons:
            raise LlmServiceError(
                f"decision cites unknown lesson: {', '.join(unknown_lessons)}"
            )

    def _record(
        self,
        *,
        snapshot: MarketSnapshot,
        brief: MarketBrief,
        lane: str,
        prompt_version: str,
        completion: CompletionResult | None,
        status: str,
        result: DecisionResult | None = None,
        raw_payload: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> dict[str, Any]:
        leverage = None if result is None else result.leverage
        decision = None if result is None else result.decision
        return {
            "schema_version": 1,
            "recorded_at": _timestamp(self._clock),
            "kind": "proposed_decision",
            "status": status,
            "lane": lane,
            "model": None if completion is None else completion.model,
            "prompt_version": prompt_version,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_hash": snapshot.content_hash,
            "snapshot_cutoff": snapshot.cutoff.isoformat(),
            "market_price": self._latest_market_price(snapshot),
            "candle_ts": self._latest_candle_ts(snapshot),
            "brief_id": brief.brief_id,
            "latency_ms": None if completion is None else completion.latency_ms,
            "usage": None if completion is None else completion.usage,
            "request_id": None if completion is None else completion.request_id,
            "decision": None if decision is None else decision.to_mapping(),
            "requested_leverage": (
                raw_payload.get("requested_leverage")
                if decision is None and raw_payload is not None
                else None if decision is None else decision.requested_leverage
            ),
            "paper_effective_leverage": None if leverage is None else leverage.paper_effective,
            "fr_retail_eligible_leverage": None if leverage is None else leverage.fr_retail_eligible,
            "excess_over_fr_retail": None if leverage is None else leverage.excess_over_fr_retail,
            "experimental_only": None if leverage is None else leverage.experimental_only,
            "leverage_clamp_reasons": None if leverage is None else list(leverage.clamp_reasons),
            "raw_payload": raw_payload,
            "error_type": None if error is None else type(error).__name__,
            "error": None if error is None else str(error)[:1000],
        }

    def _latest_market_price(self, snapshot: MarketSnapshot) -> float | None:
        candles = snapshot.candles.get(self.decision_timeframe, [])
        return None if not candles else float(candles[-1]["close"])

    def _latest_candle_ts(self, snapshot: MarketSnapshot) -> int | None:
        candles = snapshot.candles.get(self.decision_timeframe, [])
        return None if not candles else int(candles[-1]["ts"])
