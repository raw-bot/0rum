---
phase: 05-backtesting-validation
plan: 03
subsystem: infra
tags: [apscheduler, scheduler, jobs, optimizer]

requires:
  - phase: 05-02
    provides: WalkForwardOptimizer.run() async entry point

provides:
  - run_optimizer(): async APScheduler job calling WalkForwardOptimizer
  - create_scheduler(): extended with run_optimizer job (IntervalTrigger 24h, max_instances=1)

affects: [05-05]

tech-stack:
  added: []
  patterns: [deferred import inside async job function, max_instances=1 concurrency guard]

key-files:
  created:
    - tests/test_backtesting/test_scheduler_wiring.py
  modified:
    - src/scheduler/jobs.py

key-decisions:
  - "IntervalTrigger(hours=24) not CronTrigger — matches optimizer_interval_hours from config"
  - "Deferred import of WalkForwardOptimizer mirrors run_pipeline() pattern"
  - "settings = get_settings() added to create_scheduler() to read optimizer_interval_hours"
  - "run_optimizer placed before run_pipeline in file — no functional impact"

patterns-established:
  - "Async scheduler job: deferred import + try/except with structlog error"

requirements-completed: [OPTIM-04]

duration: 25min
completed: 2026-04-23
---

# Phase 05-03: APScheduler Wiring for run_optimizer()

**run_optimizer() job wired into create_scheduler() as IntervalTrigger(hours=24) with max_instances=1 — optimizer runs automatically without manual intervention**

## Performance

- **Duration:** 25 min
- **Completed:** 2026-04-23T (session 2026-04-23)
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments

- `run_optimizer()` added to `src/scheduler/jobs.py` with deferred import of `WalkForwardOptimizer`
- `create_scheduler()` extended: `settings = get_settings()` added, optimizer job registered last
- `IntervalTrigger(hours=settings.optimizer_interval_hours)` — config-driven, defaults to 24h
- `max_instances=1` prevents concurrent optimizer runs (STRIDE T-05-07)
- Exception path: `log.error("jobs.optimizer.failed")` — no silent failure (STRIDE T-05-08)
- 4 new scheduler wiring tests — all green; full suite 185/185 passed

## Task Commits

1. **Task 1: run_optimizer + create_scheduler extension + tests** - `1dd1244` (feat)

## Files Created/Modified

- `src/scheduler/jobs.py` — `run_optimizer()` async function + `scheduler.add_job()` + `get_settings()` call in `create_scheduler()`
- `tests/test_backtesting/test_scheduler_wiring.py` — 4 tests: job registered, max_instances=1, regression guard for all 6 job IDs, async function check

## Decisions Made

- `settings = get_settings()` added to `create_scheduler()` — required to pass `optimizer_interval_hours` to the trigger
- `run_optimizer` placed before `run_pipeline` in source file — no functional impact, plan specified "after run_pipeline" but ordering is cosmetic only

## Deviations from Plan

None — plan executed exactly as written. The placement order (before vs after run_pipeline) is not a behavioral deviation.

## Issues Encountered

- System Python (`/opt/homebrew/bin/pytest`) lacks `apscheduler` — tests must run via `.venv/bin/pytest`. Full suite confirmed 185/185 with `.venv/bin/pytest`.

## Next Phase Readiness

- Wave 1 complete: 05-01, 05-02, 05-03 all implemented and tested
- Wave 2 (05-04) is a human decision gate: Chemin A (wait for live data) vs Chemin B (HistData.com import)
- Decision has not been made — 05-04 must not execute before explicit path selection

---
*Phase: 05-backtesting-validation*
*Completed: 2026-04-23*
