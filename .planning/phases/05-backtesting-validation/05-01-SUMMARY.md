---
phase: 05-backtesting-validation
plan: 01
subsystem: testing
tags: [backtesting, walk-forward, monte-carlo, scipy, numpy]

requires:
  - phase: 04-signal-pipeline
    provides: OptimizerResultORM, strategy PARAM_RANGES contract

provides:
  - walk_forward.py: WalkForwardWindow, build_windows(), simulate_trade_outcome(), multi_window_gate_passes(), WFE, profit factor computation
  - monte_carlo.py: run_monte_carlo(), monte_carlo_passes() bootstrap resampling
  - test fixtures: conftest.py with make_candle() synthetic candle factory

affects: [05-02, 05-03]

tech-stack:
  added: [scipy.stats.qmc (LatinHypercube used in 05-02), numpy]
  patterns: [pure-sync computation modules called by async orchestrator, oldest→newest candle invariant preserved]

key-files:
  created:
    - src/backtesting/walk_forward.py
    - src/backtesting/monte_carlo.py
    - src/backtesting/__init__.py
    - tests/test_backtesting/__init__.py
    - tests/test_backtesting/conftest.py
    - tests/test_backtesting/test_walk_forward.py
    - tests/test_backtesting/test_monte_carlo.py
  modified: []

key-decisions:
  - "All computation is synchronous — no asyncio inside walk_forward.py or monte_carlo.py"
  - "Candle sequences passed oldest→newest per CLAUDE.md invariant"
  - "MIN_CANDLES_FOR_OPTIMIZER enforced in walk_forward._check_sufficient_data() not in optimizer"

patterns-established:
  - "Computation core: pure sync modules with no DB access, called by async orchestrator"
  - "Test fixtures: MagicMock candles with Decimal-castable fields (float() cast required for numpy)"

requirements-completed: []

duration: prior session
completed: 2026-04-23
---

# Phase 05-01: Walk-Forward and Monte Carlo Computation Modules

**Pure-sync walk-forward evaluation engine and Monte Carlo bootstrap validator — the computation core called by the async optimizer orchestrator**

## Performance

- **Duration:** prior session
- **Completed:** 2026-04-23
- **Tasks:** 2
- **Files modified:** 7 created

## Accomplishments

- `walk_forward.py`: rolling window builder, per-trade P&L simulation, WFE computation, multi-window gate, profit factor, data guard
- `monte_carlo.py`: bootstrap resampling of OOS P&L vector with configurable iterations and Sharpe/drawdown gating
- 38 tests covering all computation paths with synthetic MagicMock candle fixtures
- No DB access — all computation is pure sync, enabling deterministic unit tests without infrastructure

## Task Commits

1. **Task 1 & 2: walk_forward + monte_carlo + tests** - `70a18ee` (feat)

## Files Created/Modified

- `src/backtesting/walk_forward.py` — WalkForwardWindow, build_windows, simulate_trade_outcome, multi_window_gate_passes, WFE, profit factor, MIN_CANDLES_FOR_OPTIMIZER
- `src/backtesting/monte_carlo.py` — run_monte_carlo, monte_carlo_passes with bootstrap resampling
- `tests/test_backtesting/conftest.py` — make_candle() factory for synthetic test fixtures
- `tests/test_backtesting/test_walk_forward.py` — coverage of all walk-forward primitives
- `tests/test_backtesting/test_monte_carlo.py` — bootstrap sampling and gate tests

## Decisions Made

- Kept computation synchronous — the async entry point lives in optimizer.py (05-02), not here
- `float()` cast applied before numpy ops on Decimal ORM fields (CLAUDE.md invariant)

## Deviations from Plan

None — plan executed as written.

## Issues Encountered

Minor: `run_monte_carlo()` docstring references a vector of `daily_pnl`, but optimizer.py passes `all_oos_pnl` (aggregated OOS trade P&L). Not a correctness bug — the resampling logic is identical — but the naming is imprecise. Not changed to avoid scope creep.

## Next Phase Readiness

- `walk_forward.py` and `monte_carlo.py` fully importable by `optimizer.py` (05-02)
- `conftest.py` make_candle() fixture shared across all test_backtesting tests

---
*Phase: 05-backtesting-validation*
*Completed: 2026-04-23*
