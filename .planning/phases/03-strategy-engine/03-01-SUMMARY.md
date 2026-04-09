---
phase: 03-strategy-engine
plan: 01
subsystem: strategy-engine
tags: [strategy, base-class, atr, swing-detection, runner, asyncio]
dependency_graph:
  requires:
    - src/models/candle.py (Candle ORM)
    - src/models/optimizer_result.py (OptimizerResultORM)
    - src/database.py (AsyncSessionLocal)
  provides:
    - src/strategies/base.py (AbstractStrategy ABC)
    - src/strategies/runner.py (StrategyRunner)
    - src/models/signal_data.py (CandidateSignal Pydantic, enums)
  affects:
    - Plans 03-02 and 03-03 (4 strategy implementations subclass AbstractStrategy)
    - Phase 4 signal pipeline (calls StrategyRunner.run())
tech_stack:
  added:
    - scipy.signal.argrelextrema (swing level detection)
    - numpy (ATR/swing array calculations)
  patterns:
    - Abstract Base Class (ABC) with @abstractmethod for generate_signals
    - Wilder's EMA smoothing for ATR (alpha=1/period, seeded with SMA)
    - asyncio.gather() for parallel param loading and strategy execution
    - Lazy imports inside run() to avoid circular import at module load
key_files:
  created:
    - src/strategies/__init__.py
    - src/strategies/base.py
    - src/strategies/runner.py
    - src/models/signal_data.py
    - tests/test_strategies/__init__.py
    - tests/test_strategies/test_base.py
  modified: []
decisions:
  - "CandidateSignal and enums defined in src/models/signal_data.py (not signal.py which is ORM-only)"
  - "StrategyRunner lazy-imports 4 strategy classes inside run() to avoid circular imports"
  - "STRATEGY_NAME class attribute required on all strategy subclasses for DB param loading"
  - "order=10 documented as STRUCTURAL in detect_swing_levels docstring per CLAUDE.md §17"
metrics:
  duration: "~20 minutes"
  completed_date: "2026-04-09T05:34:48Z"
  tasks_completed: 2
  files_created: 6
  files_modified: 0
---

# Phase 3 Plan 1: Strategy Engine Foundation Summary

AbstractStrategy ABC with Wilder ATR + scipy swing detection, plus StrategyRunner loading DB params with midpoint fallback and running all 4 strategies in parallel via asyncio.gather().

## What Was Built

### Task 1: AbstractStrategy base class (TDD)

`src/strategies/base.py` — Abstract base class defining the contract for all 4 strategy implementations:

- `PARAM_RANGES: dict[str, tuple[float, float]] = {}` — each subclass declares up to 3 optimisable params
- `generate_signals(candles: dict[str, list]) -> list[CandidateSignal]` — abstract, must be implemented by each subclass
- `calculate_atr(candles, period=14) -> float` — Wilder's smoothing: TR = max(H-L, |H-prev_C|, |L-prev_C|), EMA with alpha=1/period seeded from SMA of first `period` TRs
- `detect_swing_levels(candles, order=10) -> tuple[list, list]` — scipy `argrelextrema` with order=10 (structural, never optimised per CLAUDE.md §17)

`src/models/signal_data.py` — All Pydantic data classes from CLAUDE.md §7:
- `CandidateSignal`, `CandleData`, `ApprovedSignal`, `TradeRecord`, `StrategyParams`, `MarketRegime`
- Enums: `Direction`, `Timeframe`, `StrategyName`, `SignalStatus`, `TradeStatus`, `MarketRegimeType`

`tests/test_strategies/test_base.py` — 12 tests covering:
- Direct instantiation raises TypeError
- calculate_atr returns positive float, handles empty/single candle, uses True Range (not just H-L)
- detect_swing_levels returns (list, list), handles insufficient candles, returns floats
- PARAM_RANGES midpoint calculation

### Task 2: StrategyRunner

`src/strategies/runner.py` — Coordinator loading params and running strategies:

- `_load_active_params(strategy_name, param_ranges)` — queries `optimizer_results WHERE is_active=TRUE AND strategy=?`, falls back to midpoints with `log.info("strategy_runner.fallback_to_midpoints", ...)`
- `_fetch_candles()` — fetches exactly 500 complete candles per timeframe (M15, H1, H4, D1), reversed to oldest→newest
- `run()` — double asyncio.gather(): first for parallel param loading, then for parallel strategy execution; catches per-strategy exceptions individually (T-03-04 mitigation); no DB writes

## Deviations from Plan

None — plan executed exactly as written.

The `__init__.py` was created per the plan specification. The `StrategyRunner` import of the 4 strategy classes is correctly deferred to `run()` using lazy imports, avoiding circular import issues at module load time.

## Threat Mitigations Applied

| Threat | Mitigation |
|--------|-----------|
| T-03-01: JSONB param tampering | Documented in docstring — each strategy's generate_signals must validate param keys and ranges; StrategyRunner passes params as-is from DB/midpoints |
| T-03-04: Strategy exception swallowing | `return_exceptions=True` in gather(); each exception logged individually with strategy name; other strategies continue unaffected |

## Known Stubs

None — AbstractStrategy and StrategyRunner are complete. The 4 strategy implementations (`liquidity_sweep.py`, `trend_continuation.py`, `breakout_expansion.py`, `ema_momentum.py`) are the subject of Plans 02 and 03. StrategyRunner lazy-imports them; they must define `STRATEGY_NAME: str` as a class attribute.

## Self-Check: PASSED

- src/strategies/base.py: FOUND
- src/strategies/runner.py: FOUND
- src/strategies/__init__.py: FOUND
- src/models/signal_data.py: FOUND
- tests/test_strategies/test_base.py: FOUND
- Commit dfc3db9 (RED tests): FOUND
- Commit a2569f6 (GREEN implementation): FOUND
- All 12 tests pass
