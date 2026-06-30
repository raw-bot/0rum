"""Trivial engine that proves the registry/interface actually works for an
engine that has nothing to do with the DSL or AK MACD -- not meant to trade.
Useful as the cheapest possible check that "pick a strategy by config" holds:
point goal.yaml's strategy_engine.name at "dummy" and the worker should boot
with no native_dsl/ak_macd import touched at all.
"""

from __future__ import annotations

from orum.strategies.base import Side, Signal, StrategyContext


class DummyEngine:
    name = "dummy"
    version = "1"
    required_timeframes: list[str] = []
    required_indicators: list[str] = []
    warmup_period = 0

    def __init__(self) -> None:
        self._fixed_side = Side.FLAT

    def init(self, config: dict) -> None:
        """`config.side`, if present, fixes what every call returns (e.g.
        "long" to test that a signal reaches the risk engine end to end).
        Defaults to FLAT (never trades) when absent."""
        cfg = config if isinstance(config, dict) else {}
        side = cfg.get("side", "flat")
        self._fixed_side = Side(side)

    def on_candle(self, candle: dict, context: StrategyContext) -> Signal | None:
        if self._fixed_side == Side.FLAT:
            return None
        return Signal(
            side=self._fixed_side,
            symbol=context.symbol,
            timeframe=context.timeframe,
            entry_reason="dummy engine: fixed test signal",
        )


ENGINE_CLASS = DummyEngine
