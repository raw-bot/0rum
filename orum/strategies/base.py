"""Standard contract every strategy engine must satisfy.

This is the seam that lets 0rum swap its signal-generation logic (native DSL,
the AK MACD brain, a future TradingView-webhook engine, anything else)
without touching risk, sizing, execution, accounting, or the dashboard. A
strategy engine ONLY produces a `Signal` (or `None`); it never opens/closes a
position, sizes anything, writes a state file, or renders UI — mirroring the
boundary `executor.py` already draws on the execution side (Phase 3.5).

Existing engines are adapted to this contract, not rewritten: `native_dsl`
wraps `orum.dsl.evaluator.evaluate`, `ak_macd` wraps
`orum.external.ak_macd.evaluate_ak_macd_verdict`. Neither engine's internal
logic changes when it gets a wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class Side(str, Enum):
    """What a signal asks for. EXIT closes a position regardless of its
    original side; it carries no direction of its own."""

    LONG = "long"
    SHORT = "short"
    FLAT = "flat"
    EXIT = "exit"


@dataclass(frozen=True)
class StrategyContext:
    """Read-only inputs a strategy engine needs to decide. No executor,
    accounting, dashboard, or file-path access is exposed here on purpose —
    a strategy that needs more than this is doing something it shouldn't."""

    candles: list[dict]
    symbol: str
    timeframe: str
    regime: dict | None = None
    candles_by_timeframe: dict[str, list[dict]] = field(default_factory=dict)


@dataclass(frozen=True)
class Signal:
    """Standard output of every strategy engine.

    `side` and `entry_reason` are the only fields a caller can rely on
    always being meaningful; everything else is optional because not every
    engine can produce it (e.g. the native DSL has no notion of confidence).
    `strategy_metadata` is the engine's private overflow — callers may log
    it but must not branch on its contents (that would recreate the coupling
    this contract exists to remove).
    """

    side: Side
    symbol: str
    timeframe: str
    entry_reason: str
    confidence: float | None = None
    invalidation_reason: str | None = None
    suggested_stop: float | None = None
    suggested_take_profit: float | None = None
    strategy_metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class StrategyEngine(Protocol):
    """Implemented by every strategy plugin. `init` runs once at load time;
    `on_candle` runs once per closed candle and is the only method allowed
    to make a trading decision."""

    name: str
    version: str
    required_timeframes: list[str]
    required_indicators: list[str]
    warmup_period: int

    def init(self, config: dict) -> None:
        """Configure the engine from its `params` block in `goal.yaml`."""

    def on_candle(self, candle: dict, context: StrategyContext) -> Signal | None:
        """Decide on a newly closed candle. Return None for no signal."""
