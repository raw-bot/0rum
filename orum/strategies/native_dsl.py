"""Adapts the existing DSL evaluator (`orum/dsl/`) to the `StrategyEngine`
contract. No DSL logic is duplicated or changed: `on_candle` is a thin
translation from `dsl_evaluator.evaluate`'s `EvalResult` to a `Signal`.

Not wired into `loop.py` yet. `loop.py` still calls `entry_signal_fired` /
`exit_signal_fired` directly; this engine exists so the cutover (a later
commit, done in shadow mode first) has something proven equivalent to switch
to. `test_native_dsl_engine_parity.py` pins that equivalence.

Two deliberate differences from `entry_signal_fired`/`exit_signal_fired`,
both moving a guardrail concern OUT of the strategy and INTO the caller,
where it belongs once a second engine exists:
  1. The offline-market freeze (`price_is_offline`) is not replicated here.
     It is not a property of the strategy's conditions; it is "should we
     even ask any strategy right now", which stays the loop's job for every
     engine, not something each engine re-implements.
  2. `on_candle` has no notion of "is a position currently open" (kept out
     of `StrategyContext` on purpose, see base.py) so it always evaluates
     both groups and returns EXIT before LONG when both happen to trigger
     on the same candle. For the shipped RSI config this is unreachable
     (entry requires rsi<=25, exit requires rsi>=60), but a future DSL
     config with unrelated entry/exit indicators could trigger both at
     once; exit-first is the safer default to pin (a future StrategyEngine
     allowed to act on EXIT with no open position is a caller-side no-op).
"""

from __future__ import annotations

from orum.dsl import evaluator as dsl_evaluator
from orum.dsl.migrate import strategy_dsl_groups
from orum.loop import strategy_direction
from orum.strategies.base import Side, Signal, StrategyContext


class NativeDslEngine:
    name = "native_dsl"
    version = "1"
    required_timeframes: list[str] = []  # driven by strategy.yaml at init(), not fixed per engine
    required_indicators: list[str] = []  # whitelist already enforced by dsl/schema.py
    warmup_period = 0  # warm-up vs available candles already enforced by dsl/schema.py

    def __init__(self) -> None:
        self._entry_group: dict = {"logic": "AND", "conditions": []}
        self._exit_group: dict = {"logic": "OR", "conditions": []}
        self._direction = "long"

    def init(self, config: dict) -> None:
        """`config` is the strategy dict as stored in `state/strategy.yaml`."""
        groups = strategy_dsl_groups(config)
        self._entry_group = groups["entry"]
        self._exit_group = groups["exit"]
        self._direction = strategy_direction(config)

    def on_candle(self, candle: dict, context: StrategyContext) -> Signal | None:
        if self._direction != "long":
            return None  # v1: long-only, same restriction as entry_signal_fired

        exit_eval = dsl_evaluator.evaluate(self._exit_group, context.candles)
        if exit_eval["triggered"]:
            return Signal(
                side=Side.EXIT,
                symbol=context.symbol,
                timeframe=context.timeframe,
                entry_reason=dsl_evaluator.summarize(self._exit_group, exit_eval),
                strategy_metadata={"eval": exit_eval},
            )

        entry_eval = dsl_evaluator.evaluate(self._entry_group, context.candles)
        if entry_eval["triggered"]:
            return Signal(
                side=Side.LONG,
                symbol=context.symbol,
                timeframe=context.timeframe,
                entry_reason=dsl_evaluator.summarize(self._entry_group, entry_eval),
                strategy_metadata={"eval": entry_eval},
            )
        return None
