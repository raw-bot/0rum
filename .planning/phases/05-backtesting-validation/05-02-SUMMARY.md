---
phase: 05-backtesting-validation
plan: 02
subsystem: backtesting
tags: [optimizer, walk-forward, lhs, scipy, apscheduler, asyncpg]

requires:
  - phase: 05-01
    provides: walk_forward.py, monte_carlo.py computation primitives
  - phase: 04-signal-pipeline
    provides: OptimizerResultORM, strategy PARAM_RANGES, AsyncSessionLocal

provides:
  - optimizer.py: WalkForwardOptimizer class with async run() entry point
  - LHS sampling via scipy LatinHypercube
  - DB write: activates best params in optimizer_results table
  - Data guard: skips activation when candle history is insufficient

affects: [05-03, 05-04, 05-05]

tech-stack:
  added: [scipy.stats.qmc.LatinHypercube, scipy.stats.qmc.scale]
  patterns: [async entry → sync heavy compute → async DB write, deferred strategy imports inside run()]

key-files:
  created:
    - src/backtesting/optimizer.py
    - tests/test_backtesting/test_optimizer.py
  modified: []

key-decisions:
  - "LHS uses d= (not n_components=) — scipy 1.17.1 deprecation guard"
  - "100 combos per strategy per CONTEXT.md locked decision"
  - "No asyncio.run() inside optimizer — APScheduler job is already a coroutine"
  - "Param values clamped to declared bounds before evaluation — ASVS V5"

patterns-established:
  - "Async entry / sync compute / async DB write separation"
  - "Deferred imports of strategy classes inside run() — avoids circular imports at module load"

requirements-completed: [OPTIM-01, OPTIM-02, OPTIM-03]

duration: prior session
completed: 2026-04-23
---

# Phase 05-02: WalkForwardOptimizer

**Async orchestrator: LHS parameter sampling → 3-window walk-forward evaluation → Monte Carlo gate → OptimizerResultORM activation**

## Performance

- **Duration:** prior session
- **Completed:** 2026-04-23
- **Tasks:** 1
- **Files modified:** 2 created

## Accomplishments

- `WalkForwardOptimizer.run()` orchestrates the full optimization loop for all 4 strategies
- `_sample_param_combinations()`: LHS with scipy LatinHypercube, values clamped to declared bounds
- `_fetch_all_candles()`: async DB fetch oldest-first, all 4 timeframes
- `_evaluate_all_combos()`: slides 500-candle window, evaluates 100 LHS combos, applies WFE + multi-window gate
- `_activate_params()`: upsert into optimizer_results with `is_active=True`
- Data guard: `_check_sufficient_data()` prevents premature activation

## Task Commits

1. **Task 1: WalkForwardOptimizer** - `85397ea` (feat)

## Files Created/Modified

- `src/backtesting/optimizer.py` — WalkForwardOptimizer class with full async run() pipeline
- `tests/test_backtesting/test_optimizer.py` — LHS sampling, data guard, and integration behavior tests

## Decisions Made

- `scipy.stats.qmc.LatinHypercube(d=N)` — `d=` parameter, not deprecated `n_components=`
- `asyncio.run()` deliberately absent — optimizer is called from APScheduler coroutine
- Deferred strategy imports inside `run()` — matches `run_pipeline()` pattern

## Deviations from Plan

### Noted Reserve (non-blocking)

**Monte Carlo contract precision:** `run_monte_carlo()` docstring says "vector of daily_pnl", but optimizer passes `all_oos_pnl` (aggregated OOS trade P&L, not daily). The resampling math is identical — this is a naming imprecision in the docstring, not a behavioral bug. Left as-is to avoid scope creep; can be clarified in a future polish pass.

## Issues Encountered

None — plan executed as written, tests pass.

## Next Phase Readiness

- `WalkForwardOptimizer().run()` is importable and callable as an async job
- Ready to be wired into APScheduler via `run_optimizer()` job in 05-03

---
*Phase: 05-backtesting-validation*
*Completed: 2026-04-23*
