"""Phase 4: end-to-end routing of an external signal in paper mode.

Composes the three previous layers into one pipeline:

    payload --submit--> (parse + dedup) --RECEIVED--> validate --ACCEPTED-->
        PaperExecutor.open/close --> position / trades.jsonl

Each stage can stop the flow: a duplicate/malformed payload never validates; a
rejected signal never executes. Every terminal outcome is logged with a reason,
so the audit trail explains why 0rum did or did not act.

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

from orum.accounting import compound_balance
from orum.dsl.migrate import risk_value
from orum.events import log_event
from orum.executor import Executor, PaperExecutor
from orum.external.bracket import (
    DEFAULT_FEE_RATE,
    DEFAULT_MAX_LEVERAGE,
    DEFAULT_MIN_REWARD_RISK,
    DEFAULT_RR,
    bracket_sizing,
    compute_bracket,
)
from orum.external.ingest import ExternalSignalStore
from orum.external.signal import ExternalSignal, ExternalSignalStatus
from orum.external.validate import (
    ValidationContext,
    timeframe_to_ms,
    validate_external_signal,
)

_OPEN_EVENTS = {"BUY_CANDIDATE", "SELL_CANDIDATE"}


def _optional_risk(strategy: dict, key: str) -> float | None:
    """Like risk_value but returns None when the key is absent, so an unset cap
    stays disabled instead of collapsing to a numeric default."""
    risk = strategy.get("risk")
    if isinstance(risk, dict) and key in risk:
        return float(risk[key])
    if key in strategy:
        return float(strategy[key])
    return None


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

        # Frozen SL/TP bracket (exit_mode="bracket"): when the signal carries a
        # baseline + recent high/low, 0rum computes the stop/target ONCE here
        # and sizes by the real price risk -- the %-stop/%-target and max_hold in
        # strategy.yaml are bypassed for this position. Computed BEFORE opening so
        # a degenerate (non-positive risk) setup is refused without a position.
        bracket = None
        if "baseline_at_entry" in signal.raw:
            bracket_direction = "short" if signal.event == "SELL_CANDIDATE" else "long"
            try:
                bracket = compute_bracket(
                    entry_price=signal.price,
                    baseline_at_entry=float(signal.raw["baseline_at_entry"]),
                    recent_low=signal.raw.get("recent_low"),
                    recent_high=signal.raw.get("recent_high"),
                    direction=bracket_direction,
                    rr=risk_value(strategy, "reward_risk_ratio", DEFAULT_RR),
                )
            except (ValueError, TypeError) as exc:
                self._log(
                    "external_signal_rejected",
                    f"bracket: {exc}", check="bracket", dedup_hash=signal.dedup_hash(),
                )
                return OrchestratorOutcome(
                    stage="validate", status=ExternalSignalStatus.REJECTED,
                    signal=signal, detail=f"bracket: {exc}",
                )

        # Risk-based sizing + safety caps computed BEFORE opening so a
        # disproportionate position (tight stop -> hidden leverage) or a trade
        # whose fee-adjusted reward/risk is too poor is refused without a
        # position ever being created.
        sizing = None
        if bracket is not None:
            equity = compound_balance(history, self.goal)
            risk_pct = risk_value(strategy, "position_size_r", 0.5) / 100.0
            sizing = bracket_sizing(
                account_equity=equity, risk_pct=risk_pct,
                risk_distance=bracket.risk_distance, entry_price=signal.price,
                reward_risk_ratio=bracket.reward_risk_ratio,
                max_leverage=risk_value(strategy, "max_leverage", DEFAULT_MAX_LEVERAGE),
                max_notional_usd=_optional_risk(strategy, "max_notional_usd"),
                fee_rate=risk_value(strategy, "fee_rate", DEFAULT_FEE_RATE),
                min_reward_risk=risk_value(strategy, "min_reward_risk", DEFAULT_MIN_REWARD_RISK),
            )
            if not sizing["accepted"]:
                self._log(
                    "external_signal_rejected",
                    f"sizing: {sizing['reject_reason']}", check="sizing",
                    dedup_hash=signal.dedup_hash(),
                )
                return OrchestratorOutcome(
                    stage="validate", status=ExternalSignalStatus.REJECTED,
                    signal=signal, detail=f"sizing: {sizing['reject_reason']}",
                )

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

        if bracket is not None and sizing is not None:
            # Sizing (incl. any safety cap) was computed and accepted above.
            position.update({
                "direction": bracket.direction,
                "exit_mode": "bracket",
                "entry_price": bracket.entry_price,
                "stop_loss_price": bracket.stop_loss_price,
                "take_profit_price": bracket.take_profit_price,
                "risk_distance": bracket.risk_distance,
                "sl_basis": bracket.sl_basis,
                "reward_risk_ratio": bracket.reward_risk_ratio,
                **sizing,
            })

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
