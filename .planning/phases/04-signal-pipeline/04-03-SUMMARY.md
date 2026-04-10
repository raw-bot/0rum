---
phase: 04-signal-pipeline
plan: "03"
subsystem: scheduler + pipeline tests
tags: [scheduler, apscheduler, pipeline, testing, unit-tests]
dependency_graph:
  requires:
    - 04-02  # PipelineRunner, ranker, quota gate
    - 03     # StrategyRunner (Phase 3)
  provides:
    - run_pipeline APScheduler job (15-min interval)
    - tests/test_pipeline/ package with 27 passing unit tests
  affects:
    - src/scheduler/jobs.py (create_scheduler now registers 5 jobs)
tech_stack:
  added: []
  patterns:
    - pytest-asyncio (asyncio_mode=auto) for async test functions
    - unittest.mock AsyncMock + MagicMock for DB session mocking
    - SimpleNamespace as lightweight candle fixture (no DB dependency)
    - Deferred imports inside APScheduler job function (circular import avoidance)
key_files:
  created:
    - tests/test_pipeline/__init__.py
    - tests/test_pipeline/test_dedup.py
    - tests/test_pipeline/test_conflict_filter.py
    - tests/test_pipeline/test_ranker.py
    - tests/test_pipeline/test_quota.py
    - tests/test_pipeline/test_regime_detector.py
    - tests/test_pipeline/test_runner.py
  modified:
    - src/scheduler/jobs.py
decisions:
  - "D-01 honored: run_pipeline calls StrategyRunner then PipelineRunner inline — no Redis queue"
  - "All pipeline imports inside run_pipeline() function to avoid circular imports at module load time"
  - "scalar_one_or_none() is synchronous in SQLAlchemy 2.0 result objects — used MagicMock not AsyncMock for ranker DB mock"
  - "SimpleNamespace used for fake candles in regime_detector tests — avoids SQLAlchemy ORM dependency"
metrics:
  duration: "~15 minutes"
  completed: "2026-04-09"
  tasks_completed: 2
  tasks_total: 2
  files_created: 7
  files_modified: 1
---

# Phase 4 Plan 03: APScheduler Pipeline Job and Pipeline Unit Tests Summary

**One-liner:** 15-minute APScheduler `run_pipeline` job wires StrategyRunner → 200-candle H1 fetch → PipelineRunner inline; 27 unit tests cover all 6 pipeline modules with frozen fixtures and mocked DB.

## What Was Built

### Task 1: APScheduler Pipeline Job (`src/scheduler/jobs.py`)

Added `async def run_pipeline()` before `create_scheduler()` and registered it with `IntervalTrigger(minutes=15)`, `id="run_pipeline"`, `max_instances=1`, `replace_existing=True`.

Execution sequence per D-01:
1. `StrategyRunner().run()` — returns `list[CandidateSignal]`
2. Early return if no candidates (avoids unnecessary H1 fetch)
3. Fetch 200 H1 candles from DB ordered `desc().limit(200)` then reversed to oldest→newest
4. `PipelineRunner().run(candidates, h1_candles)` — full pipeline execution

All imports (`StrategyRunner`, `PipelineRunner`, `Candle`, `select`, `AsyncSessionLocal`) are deferred inside the function body to prevent circular imports at module load time — same pattern as StrategyRunner itself.

Error handling: `try/except Exception` with `log.error("jobs.pipeline.failed", error=str(exc))` — T-04-11 mitigation (no silent failures).

### Task 2: Pipeline Unit Tests (`tests/test_pipeline/`)

27 tests across 6 modules, all passing, zero live dependencies:

| File | Tests | Coverage |
|------|-------|---------|
| `test_dedup.py` | 5 | Same-strategy dedup, direction exclusion, 0.1% price threshold, empty input, different-strategy independence |
| `test_conflict_filter.py` | 5 | BUY wins, SELL wins, same-direction all kept, empty input, tie → BUY preferred |
| `test_ranker.py` | 6 | REGIME_ALIGNMENT_MAP constants, sorted descending output, WFE fallback=0.5, empty input |
| `test_quota.py` | 4 | All pass (0 existing), partial slots (4 existing), fully exhausted (5 existing), empty input |
| `test_regime_detector.py` | 5 | HIGH_VOL override, TRENDING_UP, TRENDING_DOWN, RANGING default, ATR returns 0.0 guard |
| `test_runner.py` | 2 | Empty candidates returns [], conflict resolved with mocked regime/rank/quota/DB |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] MagicMock vs AsyncMock for scalar_one_or_none in ranker tests**
- **Found during:** Task 2, first test run
- **Issue:** `scalar_one_or_none()` in SQLAlchemy 2.0 result objects is a synchronous call. The plan's test code used `AsyncMock` for `mock_result.scalar_one_or_none`, causing `AttributeError: 'coroutine' object has no attribute 'wfe'`
- **Fix:** Changed `mock_result = AsyncMock()` with `mock_result.scalar_one_or_none = AsyncMock(return_value=None)` to `mock_result = MagicMock()` with `mock_result.scalar_one_or_none.return_value = None`
- **Files modified:** `tests/test_pipeline/test_ranker.py`
- **Commit:** befa36a (included in test commit after fix)

## Known Stubs

None — all pipeline modules are fully implemented and wired. No placeholder data flows to any output.

## Threat Flags

No new security-relevant surface introduced. The `run_pipeline` job fetches only from internal PostgreSQL with a fixed `LIMIT 200` (T-04-13 mitigation) and no user-controllable input in the WHERE clause (T-04-12 accepted).

## Self-Check

### Files exist
- `src/scheduler/jobs.py` — contains `async def run_pipeline():` ✓
- `tests/test_pipeline/__init__.py` ✓
- `tests/test_pipeline/test_dedup.py` (5 tests) ✓
- `tests/test_pipeline/test_conflict_filter.py` (5 tests) ✓
- `tests/test_pipeline/test_ranker.py` (6 tests) ✓
- `tests/test_pipeline/test_quota.py` (4 tests) ✓
- `tests/test_pipeline/test_regime_detector.py` (5 tests) ✓
- `tests/test_pipeline/test_runner.py` (2 tests) ✓

### Commits exist
- `ff86f84` feat(04-03): add run_pipeline 15-min APScheduler job ✓
- `befa36a` test(04-03): add pipeline unit tests for all 6 pipeline modules ✓

### Verification commands
- `python -c "from src.scheduler.jobs import create_scheduler; ..."` → OK ✓
- `pytest tests/test_pipeline/ -v` → 27 passed ✓

## Self-Check: PASSED
