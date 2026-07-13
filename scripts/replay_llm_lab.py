"""Offline replay for recorded LLM decisions and historical closed candles."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orum.llm.comparison import compare_lanes
from orum.llm.contracts import ProposedDecision
from orum.llm.journal import JsonlJournal
from orum.llm.lessons import LessonBook, LessonCandidate, MarketCase
from orum.llm.leverage import apply_leverage_policy
from orum.llm.outcomes import OutcomeEvaluator
from orum.llm.paper_contracts import LlmPaperAccount
from orum.llm.paper_simulator import LlmPaperSimulator
from orum.llm.paper_validator import PaperDecisionValidator


LANES = ("llm_reference", "llm_evolving")


def default_fixture() -> dict:
    """Return a deterministic aggressive fixture with no remote dependencies."""
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    first_ts = int(now.timestamp() * 1000)
    collision_ts = int((now + timedelta(minutes=15)).timestamp() * 1000)

    def decision(
        *, decision_id: str, lane: str, action: str, leverage: float,
        stop: float, target: float,
    ) -> dict:
        return {
            "decision_id": decision_id,
            "created_at": now.isoformat(),
            "lane": lane,
            "symbol": "BTC/USDT",
            "horizon": "4h",
            "action": action,
            "equity_fraction": 0.1,
            "requested_leverage": leverage,
            "order_type": "market",
            "limit_price": None,
            "stop_loss": stop,
            "take_profits": [{"price": target, "fraction": 1}],
            "trailing_stop_pct": None,
            "time_exit_minutes": 240,
            "confidence": 0.72,
            "thesis": "Replay agressif sur cassure et absorption.",
            "counter_thesis": "La même bougie peut invalider brutalement le setup.",
            "risk_rationale": "Levier volontairement agressif, paper uniquement.",
            "invalidation": "Liquidation ou stop mécanique.",
            "memo_fr": "Je teste une exposition agressive et entièrement simulée.",
            "evidence_ids": [],
            "lesson_ids": [],
        }

    long = decision(
        decision_id="replay-long-20x",
        lane="llm_reference",
        action="open_long",
        leverage=20,
        stop=96_000,
        target=105_000,
    )
    short = decision(
        decision_id="replay-short-40x",
        lane="llm_evolving",
        action="open_short",
        leverage=40,
        stop=101_500,
        target=97_000,
    )
    case = {
        "symbol": "BTC/USDT",
        "regime": "volatile_range",
        "volatility_bucket": "high",
        "side": "long",
        "action": "open_long",
        "funding_sign": "positive",
        "oi_change_bucket": "rising",
        "narrative_class": "crowded_breakout",
        "exposure_bucket": "low",
    }
    return {
        "schema_version": 1,
        "evaluated_at": now.isoformat(),
        "starting_balance_usd": 10_000,
        "paper_min_leverage": 1,
        "paper_max_leverage": 40,
        "jurisdiction_profile": "fr_retail",
        "fee_rate": 0.0005,
        "maintenance_margin_rate": 0.005,
        "decisions": [long, short, dict(long)],
        "candles": [
            {
                "ts": first_ts,
                "open": 100_000,
                "high": 100_500,
                "low": 99_500,
                "close": 100_000,
                "volume": 100,
            },
            {
                "ts": collision_ts,
                "open": 100_000,
                "high": 106_000,
                "low": 94_000,
                "close": 100_000,
                "volume": 200,
            },
        ],
        "lesson_candidates": [
            {
                "error_category": "leverage_size_mismatch",
                "conditions": case,
                "adjustment": "Réduire le levier quand la bougie couvre stop et liquidation.",
                "decision_id": "replay-long-20x",
                "evidence_strength": 0.75,
            },
            {
                "error_category": "leverage_size_mismatch",
                "conditions": case,
                "adjustment": "Réduire le levier quand la bougie couvre stop et liquidation.",
                "decision_id": "replay-short-40x",
                "evidence_strength": 0.82,
            },
        ],
    }


def _timestamp(value: object) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("replay timestamps must include a timezone")
    return stamp.astimezone(UTC)


def _case(value: Mapping[str, object]) -> MarketCase:
    return MarketCase.from_mapping(value)


def run_replay(payload: Mapping[str, object]) -> dict:
    """Replay recorded decisions locally; never imports or calls a model client."""
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ValueError("replay fixture must use schema_version 1")
    raw_decisions = payload.get("decisions")
    candles = payload.get("candles")
    if (
        isinstance(raw_decisions, (str, bytes))
        or not isinstance(raw_decisions, Sequence)
        or isinstance(candles, (str, bytes))
        or not isinstance(candles, Sequence)
        or len(candles) < 2
    ):
        raise ValueError("replay requires decisions and at least two closed candles")
    normalized_candles = [dict(item) for item in candles if isinstance(item, Mapping)]
    if len(normalized_candles) != len(candles):
        raise ValueError("every replay candle must be an object")
    candle_timestamps = [int(item["ts"]) for item in normalized_candles]
    if candle_timestamps != sorted(candle_timestamps) or len(set(candle_timestamps)) != len(candle_timestamps):
        raise ValueError("replay candles must be strictly chronological and unique")
    for candle in normalized_candles:
        open_price, high, low, close = (
            float(candle[name]) for name in ("open", "high", "low", "close")
        )
        if low > min(open_price, close) or high < max(open_price, close) or low > high:
            raise ValueError("replay candle has invalid OHLC geometry")
    now = _timestamp(payload["evaluated_at"])
    starting_balance = float(payload.get("starting_balance_usd", 10_000))
    fee_rate = float(payload.get("fee_rate", 0.0005))
    maintenance = float(payload.get("maintenance_margin_rate", 0.005))
    simulator = LlmPaperSimulator(
        fee_rate=fee_rate,
        maintenance_margin_rate=maintenance,
    )
    validator = PaperDecisionValidator(
        max_snapshot_age=timedelta(days=36500),
        maintenance_margin_rate=maintenance,
    )
    evaluator = OutcomeEvaluator(fee_rate=fee_rate)
    accounts = {
        lane: LlmPaperAccount(
            lane=lane,
            starting_balance_usd=starting_balance,
            balance_usd=starting_balance,
        )
        for lane in LANES
    }
    decision_rows: list[dict] = []
    fill_rows: list[dict] = []
    outcome_rows: list[dict] = []
    parsed_decisions = []
    for index, raw in enumerate(raw_decisions):
        if not isinstance(raw, Mapping):
            raise ValueError("every replay decision must be an object")
        decision = ProposedDecision.from_mapping(raw)
        if decision.lane not in accounts:
            raise ValueError(f"unsupported replay lane: {decision.lane}")
        parsed_decisions.append((decision.created_at, index, decision))
    parsed_decisions.sort(key=lambda item: (item[0], item[1]))
    pending_index = 0
    interval_ms = candle_timestamps[1] - candle_timestamps[0]
    decision_by_id = {item[2].decision_id: item[2] for item in parsed_decisions}

    for candle_index, candle in enumerate(normalized_candles):
        for lane in LANES:
            result = simulator.monitor_candle(accounts[lane], candle)
            accounts[lane] = result.account
            fill_rows.extend(fill.to_mapping() for fill in result.fills)
        close_ts = (
            candle_timestamps[candle_index + 1]
            if candle_index + 1 < len(candle_timestamps)
            else candle_timestamps[candle_index] + interval_ms
        )
        while pending_index < len(parsed_decisions):
            _, _, decision = parsed_decisions[pending_index]
            if int(decision.created_at.timestamp() * 1000) > close_ts:
                break
            pending_index += 1
            leverage = None
            if decision.action in {"open_long", "open_short", "add"}:
                leverage = apply_leverage_policy(
                    requested=decision.requested_leverage,
                    paper_min=float(payload.get("paper_min_leverage", 1)),
                    paper_max=float(payload.get("paper_max_leverage", 40)),
                    jurisdiction_profile=str(payload.get("jurisdiction_profile", "fr_retail")),
                    asset_class="crypto", product_kind="perpetual",
                )
            account = accounts[decision.lane]
            market_price = float(candle["close"])
            report = validator.validate(
                decision=decision, account=account, leverage=leverage,
                market_price=market_price, snapshot_cutoff=decision.created_at,
                now=decision.created_at,
            )
            decision_rows.append({
                "decision_id": decision.decision_id, "lane": decision.lane,
                "action": decision.action,
                "status": "executed" if report.accepted else "rejected",
                "reasons": list(report.reasons),
                "requested_leverage": decision.requested_leverage,
                "paper_effective_leverage": None if leverage is None else leverage.paper_effective,
                "fr_retail_eligible_leverage": None if leverage is None else leverage.fr_retail_eligible,
                "experimental_only": None if leverage is None else leverage.experimental_only,
            })
            if report.accepted:
                result = simulator.apply_decision(
                    account, decision, leverage, price=market_price,
                    candle_ts=int(candle["ts"]),
                )
                accounts[decision.lane] = result.account
                fill_rows.extend(fill.to_mapping() for fill in result.fills)

    for _, _, decision in parsed_decisions[pending_index:]:
        decision_rows.append({
            "decision_id": decision.decision_id, "lane": decision.lane,
            "action": decision.action, "status": "rejected",
            "reasons": ["no_closed_candle_after_decision"],
            "requested_leverage": decision.requested_leverage,
            "paper_effective_leverage": None,
            "fr_retail_eligible_leverage": None, "experimental_only": None,
        })

    for decision_id, decision in decision_by_id.items():
        related = [row for row in fill_rows if row.get("position_id") == f"pos-{hashlib.sha256(f'{decision.lane}|{decision_id}'.encode()).hexdigest()[:24]}"]
        entries = [row for row in related if row.get("action") in {"open", "add"}]
        exits = [row for row in related if row.get("action") not in {"open", "add"}]
        if not entries or sum(float(row["qty"]) for row in exits) + 1e-12 < sum(float(row["qty"]) for row in entries):
            continue
        open_ts = int(entries[0]["candle_ts"])
        exit_ts = int(exits[-1]["candle_ts"])
        outcome = evaluator.evaluate_fills(
            decision_id=decision_id, lane=decision.lane, symbol=decision.symbol,
            side="long" if decision.action == "open_long" else "short",
            confidence=decision.confidence, equity_fraction=decision.equity_fraction,
            fills=related,
            candles=[row for row in normalized_candles if open_ts < int(row["ts"]) <= exit_ts],
            evaluated_at=now,
        )
        outcome_rows.append(outcome.to_mapping())

    raw_candidates = payload.get("lesson_candidates", [])
    if isinstance(raw_candidates, (str, bytes)) or not isinstance(raw_candidates, Sequence):
        raise ValueError("lesson_candidates must be a list")
    with tempfile.TemporaryDirectory(prefix="0rum-llm-replay-") as temporary:
        book = LessonBook(
            JsonlJournal(Path(temporary) / "lessons.jsonl"),
            clock=lambda: now,
        )
        lesson_rows: list[dict] = []
        retrieval_case = None
        for raw in raw_candidates:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("conditions"), Mapping):
                raise ValueError("lesson candidate must contain structured conditions")
            conditions = _case(raw["conditions"])
            retrieval_case = conditions
            lesson = book.record(LessonCandidate(
                error_category=str(raw["error_category"]),
                conditions=conditions,
                adjustment=str(raw["adjustment"]),
                decision_id=str(raw["decision_id"]),
                evidence_strength=float(raw["evidence_strength"]),
                created_at=now,
                expires_at=now + timedelta(days=90),
            ))
            lesson_rows.append(lesson.to_mapping())
        evolving_lessons = (
            () if retrieval_case is None else book.retrieve(retrieval_case, limit=5)
        )

    result = {
        "schema_version": 1,
        "offline": True,
        "model_calls": 0,
        "evaluated_at": now.isoformat(),
        "decisions": decision_rows,
        "fills": fill_rows,
        "outcomes": outcome_rows,
        "accounts": {lane: accounts[lane].to_mapping() for lane in LANES},
        "comparison": compare_lanes(outcome_rows),
        "lessons": lesson_rows,
        "retrieved_lessons": {
            "llm_reference": [],
            "llm_evolving": [lesson.lesson_id for lesson in evolving_lessons],
        },
    }
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result["replay_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Recorded JSON fixture; built-in fixture when omitted")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = (
            default_fixture()
            if args.input is None
            else json.loads(args.input.read_text(encoding="utf-8"))
        )
        report = run_replay(payload)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"replay failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
