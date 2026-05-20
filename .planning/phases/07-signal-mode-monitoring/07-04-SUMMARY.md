---
phase: 07-signal-mode-monitoring
plan: "04"
subsystem: scheduler/monitoring
tags: [scheduler, monitor_trades, daily_summary, strategy_stats, circuit_breaker, apscheduler]
dependency_graph:
  requires:
    - "07-01"  # TradeORM with trailing_stop_price
    - "07-02"  # NotificationAdapter.send_lifecycle_notification + DailySummaryPayload
    - "07-03"  # ExecutionRouter + PipelineRunner creating trades
  provides:
    - monitor_trades APScheduler job (15-min interval, OPEN/TP1_HIT state machine)
    - daily_summary APScheduler cron job (00:00 UTC, full D-19 payload)
    - BreakerManager.get_consecutive_stops()
  affects:
    - src/scheduler/jobs.py (two new jobs + create_scheduler registration)
    - src/risk/breaker.py (get_consecutive_stops method)
tech_stack:
  added: []
  patterns:
    - APScheduler IntervalTrigger + CronTrigger for job scheduling
    - PostgreSQL INSERT ... ON CONFLICT DO UPDATE for strategy_stats upsert
    - Deferred imports inside async functions to avoid top-level DB init at import time
    - asynccontextmanager mock pattern for async DB session testing
key_files:
  created:
    - tests/test_monitoring/test_monitor_trades.py
    - tests/test_monitoring/test_strategy_stats.py
  modified:
    - src/scheduler/jobs.py
    - src/risk/breaker.py
decisions:
  - "Patch src.database.AsyncSessionLocal not src.scheduler.jobs.AsyncSessionLocal — deferred imports mean the module has no top-level AsyncSessionLocal attribute; patching the source module ensures all deferred imports see the mock"
  - "Evict src.scheduler.jobs stub in test_monitor_trades module-level setup — test_health_risk.py injects an empty ModuleType stub before our tests are collected; evicting it forces re-import of the real module"
  - "trail_touched() reads trade.trailing_stop_price AFTER the ratchet update — this is correct; the ratchet runs first, then close checks use the updated value (reflects current candle's highest achieved trail)"
  - "TP2 test uses atr_h1=2.0 and candle_low=2378.50 — ensures ratcheted trail (candle_high - atr = 2380 - 2 = 2378) stays below candle_low so only TP2 fires, not TRAIL (D-05)"
metrics:
  duration: "~25 minutes"
  completed: "2026-05-02"
  tasks_completed: 2
  tasks_total: 2
  files_changed: 4
---

# Phase 7 Plan 04: Monitor Trades + Daily Summary Summary

Implemented the two new APScheduler jobs that drive the SIG-02 and SIG-03 requirements: `monitor_trades` (15-min interval, full OPEN→TP1_HIT→CLOSED state machine) and `daily_summary` (cron 00:00 UTC, D-19 payload sent via NotificationAdapter).

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | monitor_trades job — full trade lifecycle state machine | 60ea09d | src/scheduler/jobs.py, tests/test_monitoring/test_monitor_trades.py |
| 2 | daily_summary job + strategy_stats tests + scheduler registration | 3eb1084 | src/scheduler/jobs.py, src/risk/breaker.py, tests/test_monitoring/test_strategy_stats.py |

## What Was Built

### `monitor_trades` (Task 1)

- Fetches latest completed M15 candle HIGH/LOW range for intra-candle level touches (D-01)
- Fetches H1 ATR(14) using `RegimeDetector._calculate_atr()` for trailing stop distance (D-02)
- Queries all `OPEN` and `TP1_HIT` trades via JOIN through ApprovedSignalORM → CandidateSignalORM to get strategy name without redundant storage (D-18)
- `_process_trade()`: direction-aware touch detection for BUY/SELL; OPEN→TP1_HIT transition with trailing stop initialization; TP1_HIT trailing stop ratchet (only updates when strictly better, D-03)
- `_close_trade()`: D-06 blended P&L (`0.5 × tp1_pnl + 0.5 × final_pnl`); D-17 strategy_stats upsert in same `session.begin()` block; win_rate and profit_factor recomputed in second UPDATE within same transaction; D-07 `BreakerManager.record_stop()` on SL close; D-08 `BreakerManager.record_win()` on profitable close
- D-05 conservative resolution: SL wins over TP1 on same-candle tie (OPEN); trailing stop wins over TP2 on same-candle tie (TP1_HIT)
- Registered with `IntervalTrigger(minutes=15)`, `max_instances=1`

### `daily_summary` (Task 2)

- Queries: signals sent today, trades by close_reason, daily P&L sum, MTD P&L sum, per-strategy stats ordered by win_rate
- Reads `BreakerManager.is_tripped()` and new `get_consecutive_stops()` for CB state
- Builds `DailySummaryPayload` and calls `_notification_adapter.send_daily_summary(payload)` (NOTIF-04)
- Registered with `CronTrigger(hour=0, minute=0, timezone="UTC")`, `max_instances=1`

### `BreakerManager.get_consecutive_stops()`

- Reads `CB_COUNTER` Redis key, returns `int` (0 if absent)

## Test Results

```
tests/test_monitoring/test_monitor_trades.py  — 9 tests (all pass)
tests/test_monitoring/test_strategy_stats.py  — 5 tests (all pass)
tests/test_monitoring/ (full suite)           — 28 tests (all pass)
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Deferred AsyncSessionLocal imports to avoid top-level DB init**
- **Found during:** Task 1 GREEN phase
- **Issue:** Moving `from src.database import AsyncSessionLocal` to module top-level caused `pydantic_settings` to validate env vars at import time — test collection failed without a live DB URL
- **Fix:** Reverted to deferred imports inside each function body (`monitor_trades`, `_process_trade`, `_close_trade`, `daily_summary`); patching changed from `src.scheduler.jobs.AsyncSessionLocal` to `src.database.AsyncSessionLocal`
- **Files modified:** src/scheduler/jobs.py, tests/test_monitoring/test_monitor_trades.py
- **Commit:** 3eb1084

**2. [Rule 3 - Blocking] Evict test_health_risk stub before monitor_trades tests**
- **Found during:** Task 2 — full suite run (`tests/test_monitoring/ -x -q`)
- **Issue:** `test_health_risk.py` injects an empty `ModuleType` stub for `src.scheduler.jobs` at collection time (alphabetically before our tests). When our tests ran second, patching `src.scheduler.jobs._breaker_manager` failed with `AttributeError` because the stub has no attributes
- **Fix:** Added `_ensure_real_jobs_module()` at top of `test_monitor_trades.py` that evicts the stub from `sys.modules` and reimports the real module if `_process_trade` is absent
- **Files modified:** tests/test_monitoring/test_monitor_trades.py
- **Commit:** 3eb1084

**3. [Rule 1 - Bug] TP2 test candle parameters corrected for ratchet interaction**
- **Found during:** Task 1 GREEN phase (test_tp1_hit_tp2_touched_closes_as_tp2 failing)
- **Issue:** Test used `atr_h1=10, candle_high=2380, candle_low=2360` — ratchet updated trail to `2380-10=2370`, then `trail_touched = 2360 <= 2370 = True`, overriding the expected TP2 close
- **Fix:** Changed to `atr_h1=2, candle_low=2378.50` so ratcheted trail `=2380-2=2378 < 2378.50` (no trail touch); same fix applied to `test_win_close_calls_record_win`
- **Files modified:** tests/test_monitoring/test_monitor_trades.py
- **Commit:** 60ea09d

## Known Stubs

None — all fields in `daily_summary` are wired to live DB queries. `_notification_adapter` and `_breaker_manager` default to `None` / fresh `BreakerManager()` until `_set_monitor_services()` is called from `main.py` (that wiring is a Phase 7 integration task, not a stub in this plan's scope).

## Threat Flags

None — all surfaces were covered in the plan's threat model (T-07-04-01 through T-07-04-04). No new network endpoints, auth paths, or schema changes introduced beyond what was planned.

## Self-Check: PASSED

- src/scheduler/jobs.py: FOUND
- src/risk/breaker.py: FOUND
- tests/test_monitoring/test_monitor_trades.py: FOUND
- tests/test_monitoring/test_strategy_stats.py: FOUND
- Commit 60ea09d: FOUND
- Commit 3eb1084: FOUND
