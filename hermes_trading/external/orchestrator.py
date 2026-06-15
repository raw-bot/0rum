"""Phase 4: end-to-end routing of an external signal in paper mode.

Composes the three previous layers into one pipeline:

    payload --submit--> (parse + dedup) --RECEIVED--> validate --ACCEPTED-->
        PaperExecutor.open/close --> position / trades.jsonl

Each stage can stop the flow: a duplicate/malformed payload never validates; a
rejected signal never executes. Every terminal outcome is logged with a reason,
so the audit trail explains why Hermes did or did not act.

Native state (position, trades, strategy, mode, drawdown) is reached through an
injected ``StateAccess`` gateway. That keeps the orchestrator testable with an
in-memory fake AND keeps the footprint on ``loop.py`` at zero — nothing here
edits the native engine; it only reads/writes the same state files it owns.

This runs ONLY in ``signal_source: tradingview_external`` mode; in ``native``
mode the engine's own DSL evaluator is the sole signal source and this
orchestrator is never invoked. The two never mix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from hermes_trading.accounting import compound_balance
from hermes_trading.events import log_event
from hermes_trading.executor import Executor, PaperExecutor
from hermes_trading.external.ingest import ExternalSignalStore
from hermes_trading.external.signal import ExternalSignal, ExternalSignalStatus
from hermes_trading.external.validate import (
    ValidationContext,
    timeframe_to_ms,
    validate_external_signal,
)

_OPEN_EVENTS = {"BUY_CANDIDATE", "SELL_CANDIDATE"}


def signal_source(goal: dict) -> str:
    """Which engine owns signals. Anything unrecognized falls back to native."""
    source = str(goal.get("signal_source", "native"))
    return source if source in ("native", "tradingview_external") else "native"


@runtime_checkable
class StateAccess(Protocol):
    """The live state the orchestrator reads/writes. Implementations decide
    where it lives (files in production, memory in tests)."""

    def load_strategy(self) -> dict: ...
    def load_position(self) -> dict | None: ...
    def save_position(self, position: dict) -> None: ...
    def clear_position(self) -> None: ...
    def append_trade(self, trade: dict) -> None: ...
    def trade_history(self) -> list[dict]: ...
    def resume_ack(self) -> bool: ...
    def trading_mode(self) -> str: ...
    def price_offline(self) -> bool: ...
    def now_ms(self) -> int: ...


@dataclass(frozen=True)
class OrchestratorOutcome:
    stage: str  # "ingest" | "validate" | "execute"
    status: ExternalSignalStatus
    signal: ExternalSignal | None
    detail: str
    trade: dict | None = None
    position: dict | None = None

    @property
    def executed(self) -> bool:
        return self.status is ExternalSignalStatus.EXECUTED


class ExternalOrchestrator:
    def __init__(
        self,
        *,
        goal: dict,
        state: StateAccess,
        store: ExternalSignalStore,
        executor: Executor | None = None,
        logger=log_event,
    ):
        self.goal = goal
        self.state = state
        self.store = store
        self.executor: Executor = executor or PaperExecutor()
        self._log = logger

    def handle(self, payload: dict | str) -> OrchestratorOutcome:
        ingest = self.store.submit(payload)
        if not ingest.admitted:
            # duplicate / malformed (rejected). Already logged by the store.
            return OrchestratorOutcome(
                stage="ingest",
                status=ingest.status,
                signal=ingest.signal,
                detail="; ".join(ingest.errors) if ingest.errors else ingest.status.value,
            )

        signal = ingest.signal
        history = self.state.trade_history()
        context = ValidationContext(
            goal=self.goal,
            open_position=self.state.load_position(),
            # max_drawdown wants the full history (matches native loop.py).
            recent_trades=tuple(history),
            resume_ack=self.state.resume_ack(),
            trading_mode=self.state.trading_mode(),
            price_offline=self.state.price_offline(),
            now_ms=self.state.now_ms(),
        )
        result = validate_external_signal(signal, context)
        if not result.accepted:
            self._log(
                "external_signal_rejected",
                f"{result.check}: {result.reason}",
                check=result.check,
                dedup_hash=signal.dedup_hash(),
                guardrail=result.guardrail,
            )
            return OrchestratorOutcome(
                stage="validate", status=ExternalSignalStatus.REJECTED, signal=signal, detail=result.reason
            )

        return self._execute(signal, history)

    def _execute(self, signal: ExternalSignal, history: list[dict]) -> OrchestratorOutcome:
        strategy = self.state.load_strategy()
        # Synthetic single-bar market: the external source provides the close.
        market = {"closes": [signal.price], "last_candle_ts": signal.bar_time, "source": signal.source}

        if signal.event == "EXIT":
            position = self.state.load_position()  # validation guaranteed it exists
            trade = self.executor.force_close(
                position=position, strategy=strategy, market=market, rsi=None, regime=None, reason="external_exit"
            )
            trade["external_signal_id"] = signal.dedup_hash()
            balance_before = compound_balance(history, self.goal)
            net = float(trade.get("net_pnl_usd", 0.0))
            trade["balance_before_usd"] = balance_before
            trade["balance_after_usd"] = balance_before + net
            trade["account_return"] = net / balance_before if balance_before > 0 else 0.0
            self.state.append_trade(trade)
            self.state.clear_position()
            self._log(
                "external_signal_executed",
                f"closed via external EXIT {signal.symbol} @ {signal.price}",
                dedup_hash=signal.dedup_hash(),
                exit_reason="external_exit",
                net_pnl_usd=net,
            )
            return OrchestratorOutcome(
                stage="execute", status=ExternalSignalStatus.EXECUTED, signal=signal, detail="closed", trade=trade
            )

        # Opening event. The position is labelled with the ENGINE asset (the
        # venue the worker's price feed and external risk tick use), not the
        # TradingView ticker -- otherwise run_loop would mark-to-market a
        # BITSTAMP entry against the Binance feed. Falls back to the TV symbol
        # when the allowlist entry declares no mapping.
        engine_asset = signal.symbol
        for entry in self.goal.get("allowed_external_strategies", []):
            if entry.get("id") == signal.strategy:
                engine_asset = entry.get("engine_asset", signal.symbol)
                break
        position = self.executor.open(
            asset=engine_asset,
            strategy=strategy,
            goal=self.goal,
            market=market,
            rsi=None,
            regime=None,
            entry_summary=f"external {signal.event} from {signal.strategy}",
        )
        position["external_signal_id"] = signal.dedup_hash()
        # Stamp the strategy's bar size so _held_candles counts external candles,
        # not 1m candles. The allowlist already vetted this timeframe.
        interval_ms = timeframe_to_ms(signal.timeframe)
        if interval_ms:
            position["candle_interval_ms"] = interval_ms
        self.state.save_position(position)
        self._log(
            "external_signal_executed",
            f"opened via external {signal.event} {signal.symbol} @ {signal.price}",
            dedup_hash=signal.dedup_hash(),
            entry_price=position.get("entry_price"),
        )
        return OrchestratorOutcome(
            stage="execute", status=ExternalSignalStatus.EXECUTED, signal=signal, detail="opened", position=position
        )
