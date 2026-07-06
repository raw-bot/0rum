"""Phase 3: business validation of an external signal against 0rum' own gates.

This is the layer that enforces the architecture rule: TradingView may *propose*
a signal, but 0rum decides. Every check below is a reason 0rum can refuse,
and a refusal always carries the failing check + a human reason for the log.

It deliberately REUSES the native gates rather than re-implementing them:
  - ``guardrail_action`` + ``max_drawdown`` for drawdown / halt / kill-switch;
  - the long-only / offline-freeze semantics that ``loop.py`` already applies.
Re-deriving those here would let the external path drift from the native path —
exactly the divergence the two-mode design forbids.

The validator is PURE: all live state arrives through ``ValidationContext`` so it
is testable without touching files, the clock, or the environment. Structural
validity (shape, types, enums, ms canonicalization) is already guaranteed by the
parser; dedup is already handled by the store. This layer assumes a parsed,
admitted signal and only judges whether acting on it is allowed *now*.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orum.accounting import max_drawdown
from orum.external.signal import ExternalSignal, ExternalSignalStatus
from orum.loop import guardrail_action

_OPEN_EVENTS = {"BUY_CANDIDATE", "SELL_CANDIDATE"}

# Minute/hour/day/week → milliseconds. Used to verify a bar is grid-aligned and
# fully closed; unknown formats are not blocked here (the allowlist already
# gates which timeframes a strategy may emit).
_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
_ONE_DAY_MS = 86_400_000


def timeframe_to_ms(timeframe: str) -> int | None:
    tf = timeframe.strip().lower()
    if len(tf) < 2 or tf[-1] not in _UNIT_MS or not tf[:-1].isdigit():
        return None
    return int(tf[:-1]) * _UNIT_MS[tf[-1]]


@dataclass(frozen=True)
class ValidationContext:
    """Live state the validator needs, injected explicitly (no globals)."""

    goal: dict
    open_position: dict | None = None
    recent_trades: tuple[dict, ...] = ()
    resume_ack: bool = False
    trading_mode: str = "paper"
    price_offline: bool = False
    already_processed: bool = False
    now_ms: int | None = None


@dataclass(frozen=True)
class ValidationResult:
    status: ExternalSignalStatus  # ACCEPTED or REJECTED
    signal: ExternalSignal
    guardrail: str
    check: str | None = None
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.status is ExternalSignalStatus.ACCEPTED


def _strategy_entry(goal: dict, strategy_id: str) -> dict | None:
    for entry in goal.get("allowed_external_strategies", []):
        if entry.get("id") == strategy_id:
            return entry
    return None


def _allowed_sources(goal: dict) -> set[str]:
    return set(goal.get("allowed_external_sources", ["tradingview"]))


def _bar_close_failure(signal: ExternalSignal, ctx: ValidationContext) -> str | None:
    tf_ms = timeframe_to_ms(signal.timeframe)
    if tf_ms is None:
        return None
    # Intraday bars open on the grid; a misaligned ts means an intrabar or
    # mis-sourced signal. Daily/weekly epoch alignment is not a clean modulo, so
    # only the close-time check applies to them.
    if tf_ms < _ONE_DAY_MS and signal.bar_time % tf_ms != 0:
        return f"bar_time {signal.bar_time} not aligned to {signal.timeframe} grid"
    if ctx.now_ms is not None and ctx.now_ms < signal.bar_time + tf_ms:
        return f"bar not closed yet (now={ctx.now_ms} < close={signal.bar_time + tf_ms})"
    return None


def _position_failure(signal: ExternalSignal, ctx: ValidationContext) -> str | None:
    if signal.event == "EXIT":
        if ctx.open_position is None:
            return "no open position to exit"
        return None
    # Opening events.
    if signal.event == "SELL_CANDIDATE" and not ctx.goal.get("allow_short", False):
        return "short entries not supported (allow_short is false); engine is long-only"
    if ctx.open_position is not None:
        return "a position is already open; cannot open another"
    return None


def validate_external_signal(signal: ExternalSignal, context: ValidationContext) -> ValidationResult:
    """Run the ordered gates; first failure short-circuits with its reason."""
    goal = context.goal
    drawdown = max_drawdown(list(context.recent_trades), goal)
    guardrail = guardrail_action(drawdown, goal, context.resume_ack)

    def reject(check: str, reason: str) -> ValidationResult:
        return ValidationResult(ExternalSignalStatus.REJECTED, signal, guardrail, check, reason)

    # 1. source authorized
    if signal.source not in _allowed_sources(goal):
        return reject("source", f"source {signal.source!r} not authorized")

    # 2. strategy known (allowlist)
    entry = _strategy_entry(goal, signal.strategy)
    if entry is None:
        return reject("strategy", f"strategy {signal.strategy!r} not in allowlist")

    # 3. symbol authorized for this strategy
    if entry.get("symbol") != signal.symbol:
        return reject("symbol", f"symbol {signal.symbol!r} not allowed for {signal.strategy!r}")

    # 4. timeframe authorized for this strategy
    if entry.get("timeframe") != signal.timeframe:
        return reject("timeframe", f"timeframe {signal.timeframe!r} not allowed for {signal.strategy!r}")

    # 5. event authorized for this strategy
    if signal.event not in entry.get("events", []):
        return reject("event", f"event {signal.event!r} not allowed for {signal.strategy!r}")

    # 6. bar_time not already processed (dedup happens upstream; this is a guard)
    if context.already_processed:
        return reject("duplicate", "bar_time already processed")

    # 7. bar closed (grid-aligned + fully closed)
    bar_failure = _bar_close_failure(signal, context)
    if bar_failure:
        return reject("bar_closed", bar_failure)

    # 8. paper/live mode coherent (live execution is deferred — paper only)
    if context.trading_mode != "paper":
        return reject("mode", f"mode {context.trading_mode!r}: live execution not available, paper only")

    # 9. price source not offline (native freezes entries AND exits when offline)
    if context.price_offline:
        return reject("price_source", "price source is offline fallback; trading frozen")

    # 10. no contradictory position
    position_failure = _position_failure(signal, context)
    if position_failure:
        return reject("position", position_failure)

    # 11. risk gates / drawdown / kill-switch — opens require a normal guardrail.
    #     Exits are never blocked (you must always be able to close).
    if signal.event in _OPEN_EVENTS and guardrail != "normal":
        return reject("guardrail", f"guardrail {guardrail!r}: entries halted (drawdown={drawdown:.4f})")

    return ValidationResult(ExternalSignalStatus.ACCEPTED, signal, guardrail)
