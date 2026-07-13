"""Mode-gated orchestration for the non-executing LLM trading laboratory."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from orum.llm.config import LlmMode, LlmTradingConfig
from orum.llm.contracts import MarketBrief, MarketSnapshot
from orum.llm.journal import JsonlJournal
from orum.llm.services import DecisionResult, LlmServiceError


PAPER_MODE_ERROR = "paper LLM execution is not installed in foundation phase"


class LlmRuntimeError(RuntimeError):
    """Raised when a requested laboratory mode cannot safely run."""


@dataclass(frozen=True, slots=True)
class LaneRunResult:
    lane: str
    status: str
    decision_id: str | None
    error: str | None = None
    paper_status: str | None = None
    fill_ids: tuple[str, ...] = ()
    rejection_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LlmRunResult:
    mode: LlmMode
    snapshot_id: str | None
    brief_id: str | None
    lanes: tuple[LaneRunResult, ...]


class LlmLabRuntime:
    """Create observable briefs and shadow decisions, without an execution path."""

    def __init__(
        self,
        *,
        config: LlmTradingConfig,
        snapshot_factory: Callable[[], MarketSnapshot],
        analyst: Any,
        reference_trader: Any,
        evolving_trader: Any,
        decision_journal: JsonlJournal,
        lesson_provider: Callable[[int], Sequence[Mapping[str, Any]]],
        paper_executor: Any | None = None,
    ) -> None:
        self.config = config
        self.snapshot_factory = snapshot_factory
        self.analyst = analyst
        self.reference_trader = reference_trader
        self.evolving_trader = evolving_trader
        self.decision_journal = decision_journal
        self.lesson_provider = lesson_provider
        self.paper_executor = paper_executor

    def run_once(self) -> LlmRunResult:
        mode = self.config.mode
        if mode is LlmMode.OFF:
            return LlmRunResult(mode=mode, snapshot_id=None, brief_id=None, lanes=())
        if mode is LlmMode.PAPER_ASSISTED:
            raise LlmRuntimeError(PAPER_MODE_ERROR)
        if mode is LlmMode.PAPER_AUTONOMOUS and self.paper_executor is None:
            raise LlmRuntimeError("paper_autonomous requires an isolated paper executor")

        snapshot = self.snapshot_factory()
        brief: MarketBrief = self.analyst.analyze(snapshot)
        if mode is LlmMode.OBSERVER:
            return LlmRunResult(
                mode=mode,
                snapshot_id=snapshot.snapshot_id,
                brief_id=brief.brief_id,
                lanes=(),
            )

        if mode not in {LlmMode.SHADOW, LlmMode.PAPER_AUTONOMOUS}:
            raise LlmRuntimeError(f"unsupported LLM laboratory mode: {mode.value}")

        lessons = tuple(self.lesson_provider(self.config.max_retrieved_lessons))
        lane_results = (
            self._run_lane(
                lane="llm_reference",
                trader=self.reference_trader,
                snapshot=snapshot,
                brief=brief,
                lessons=(),
            ),
            self._run_lane(
                lane="llm_evolving",
                trader=self.evolving_trader,
                snapshot=snapshot,
                brief=brief,
                lessons=lessons,
            ),
        )
        return LlmRunResult(
            mode=mode,
            snapshot_id=snapshot.snapshot_id,
            brief_id=brief.brief_id,
            lanes=lane_results,
        )

    def _run_lane(
        self,
        *,
        lane: str,
        trader: Any,
        snapshot: MarketSnapshot,
        brief: MarketBrief,
        lessons: Sequence[Mapping[str, Any]],
    ) -> LaneRunResult:
        existing = self._existing_decision_id(snapshot.snapshot_id, lane)
        if existing is not None:
            return LaneRunResult(
                lane=lane,
                status="skipped_duplicate",
                decision_id=existing,
            )
        try:
            result: DecisionResult = trader.decide(
                snapshot,
                brief,
                lane=lane,
                lessons=lessons,
            )
        except LlmServiceError as exc:
            return LaneRunResult(
                lane=lane,
                status="model_error",
                decision_id=None,
                error=str(exc),
            )
        paper = None
        if self.config.mode is LlmMode.PAPER_AUTONOMOUS:
            candles = snapshot.candles.get(self.config.decision_timeframe)
            if not isinstance(candles, list) or not candles:
                raise LlmRuntimeError("decision timeframe has no closed candle")
            latest = candles[-1]
            paper = self.paper_executor.execute(
                decision=result.decision,
                leverage=result.leverage,
                market_price=float(latest["close"]),
                snapshot_id=snapshot.snapshot_id,
                snapshot_hash=snapshot.content_hash,
                snapshot_cutoff=snapshot.cutoff,
                candle_ts=int(latest["ts"]),
            )
        return LaneRunResult(
            lane=lane,
            status="created",
            decision_id=result.decision.decision_id,
            paper_status=None if paper is None else paper.status,
            fill_ids=() if paper is None else paper.fill_ids,
            rejection_reasons=() if paper is None else paper.reasons,
        )

    def _existing_decision_id(self, snapshot_id: str, lane: str) -> str | None:
        for record in reversed(self.decision_journal.read()):
            if (
                record.get("kind") != "proposed_decision"
                or record.get("status") != "valid"
                or record.get("snapshot_id") != snapshot_id
                or record.get("lane") != lane
            ):
                continue
            decision = record.get("decision")
            if not isinstance(decision, Mapping):
                continue
            decision_id = decision.get("decision_id")
            if isinstance(decision_id, str) and decision_id.strip():
                return decision_id.strip()
        return None
