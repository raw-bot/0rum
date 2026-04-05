---
phase: 02-data-ingestion
plan: "02-02"
subsystem: ingestion
tags: [apscheduler, gap-detection, scheduler, fastapi-lifespan, asyncio, structlog]

# Dependency graph
requires:
  - phase: 02-data-ingestion
    plan: 02-01
    provides: CandleFetcher.fetch_and_store(), backfill_all(), prune_incomplete()
  - phase: 01-foundation
    provides: Candle ORM, AsyncSessionLocal, FastAPI app with lifespan
provides:
  - GapDetector with find_gaps() and detect_and_fill() for temporal gap scanning
  - APScheduler with 4 candle refresh jobs (M15/H1/H4/D1) all with max_instances=1
  - FastAPI lifespan wired with asyncio.create_task(backfill_all()) and scheduler.start()/shutdown()
  - get_last_candle_fetch() wired into /health last_candle_fetch field
affects:
  - 03-strategy-engine (candles now refreshed continuously, gaps filled automatically)
  - /health endpoint (last_candle_fetch now returns real timestamps instead of None)

# Tech tracking
tech-stack:
  added: [apscheduler (AsyncIOScheduler, IntervalTrigger, CronTrigger)]
  patterns:
    - "GapDetector.detect_and_fill() is one-pass only — no retry loop, no recursion"
    - "Weekend gaps (Fri close to Sun open, up to 48h) tolerated via weekday()==4 check"
    - "APScheduler max_instances=1 on all jobs prevents pile-up on slow fetches"
    - "asyncio.create_task() for non-blocking startup backfill — lifespan yield proceeds immediately"
    - "GIL-protected dict assignment for _last_candle_fetch — safe for concurrent async tasks"

key-files:
  created:
    - src/ingestion/gap_detector.py
    - src/scheduler/__init__.py
    - src/scheduler/jobs.py
  modified:
    - src/main.py
    - src/monitoring/health.py

key-decisions:
  - "Gap detection is one-pass per call — if fetch returns 0 candles, log as unresolved gap and continue (no recursive retry loop per threat model)"
  - "max_instances=1 on all APScheduler jobs prevents resource exhaustion when a fetch takes longer than its interval"
  - "backfill_all() launched via asyncio.create_task() not awaited — lifespan yield and scheduler.start() proceed immediately (non-blocking startup)"
  - "Weekend gap tolerance uses weekday()==4 (Friday) + delta <= 172800s (48h) — covers Fri close to Sun open window"
  - "_last_candle_fetch is a module-level dict — GIL-protected atomic dict assignment is sufficient for CPython concurrent async tasks"

requirements-completed: [DATA-01, DATA-02]

# Metrics
duration: 2min
completed: 2026-04-06
---

# Phase 02 Plan 02: Gap Detector, Scheduler, and Startup Wiring Summary

**APScheduler with 4 timeframe candle refresh jobs plus GapDetector for temporal gap scanning, wired into FastAPI lifespan with non-blocking startup backfill via asyncio.create_task()**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-04-06T00:51:42Z
- **Completed:** 2026-04-06T00:53:49Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- GapDetector.find_gaps() scans candle timestamps ordered ascending, flags gaps > 2x expected interval, tolerates weekend gaps (Fri weekday=4, delta <= 48h)
- GapDetector.detect_and_fill() is strictly one-pass — no retry loop, no recursion; logs gap_detected, gap_filled, gap_unresolved, fill_error via structlog
- APScheduler creates exactly 4 jobs: M15 (IntervalTrigger/15min), H1 (IntervalTrigger/1h), H4 (IntervalTrigger/4h), D1 (CronTrigger/00:05 UTC); all max_instances=1
- Each scheduled job calls fetch_and_store() → detect_and_fill() → prune_incomplete() in sequence
- FastAPI lifespan launches backfill_all() as asyncio.create_task() (non-blocking), then starts scheduler before yield; shutdown calls scheduler.shutdown(wait=False)
- /health last_candle_fetch field wired to get_last_candle_fetch() — returns per-timeframe ISO timestamps instead of None stub from plan 02-01

## Task Commits

Each task was committed atomically:

1. **Task 1: Gap detector with structured logging** - `df881bb` (feat)
2. **Task 2: APScheduler jobs and startup wiring in main.py** - `daaa489` (feat)

## Files Created/Modified

- `src/ingestion/gap_detector.py` - GapDetector: find_gaps(), detect_and_fill(), TIMEFRAME_MINUTES map, Gap NamedTuple
- `src/scheduler/__init__.py` - Package marker (empty)
- `src/scheduler/jobs.py` - create_scheduler(), 4 job functions, _refresh_timeframe(), get_last_candle_fetch()
- `src/main.py` - Added asyncio import, backfill_all() task launch, create_scheduler()/start()/shutdown() in lifespan
- `src/monitoring/health.py` - Imported get_last_candle_fetch(), replaced hardcoded None stub

## Decisions Made

- Gap detection is one-pass per call — fetch returns 0 candles → log as unresolved gap and continue (no recursive retry loop per threat model)
- max_instances=1 on all APScheduler jobs prevents resource exhaustion when a fetch takes longer than its interval
- backfill_all() launched via asyncio.create_task() not awaited — lifespan yield and scheduler.start() proceed immediately (non-blocking startup)
- Weekend gap tolerance uses weekday()==4 (Friday) + delta <= 172800s (48h) — covers Fri close to Sun open window
- _last_candle_fetch is a module-level dict — GIL-protected atomic dict assignment is sufficient for CPython concurrent async tasks

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None — get_last_candle_fetch() is fully wired. The /health last_candle_fetch field now returns actual per-timeframe ISO timestamps once scheduler jobs have run (None per timeframe until first successful fetch, which is correct initial state).

## Threat Flags

No new security-relevant surface introduced beyond the plan's threat model. All mitigations from the threat model are implemented:
- No retry loop in detect_and_fill() (DoS mitigation)
- max_instances=1 on all jobs (resource exhaustion mitigation)
- asyncio.create_task() for backfill (availability mitigation)
- GIL-protected dict for _last_candle_fetch (data integrity — documented)
- ON CONFLICT DO NOTHING in fetch_and_store() (idempotent restart mitigation, inherited from 02-01)

## Self-Check: PASSED

Files verified:
- src/ingestion/gap_detector.py: FOUND
- src/scheduler/__init__.py: FOUND
- src/scheduler/jobs.py: FOUND
- src/main.py: modified with asyncio.create_task and scheduler wiring: FOUND
- src/monitoring/health.py: get_last_candle_fetch wired: FOUND

Commits verified:
- df881bb (Task 1): FOUND
- daaa489 (Task 2): FOUND
