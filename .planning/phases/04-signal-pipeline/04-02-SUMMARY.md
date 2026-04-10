---
phase: 04-signal-pipeline
plan: "02"
subsystem: pipeline
tags: [ranker, quota, pipeline-runner, regime-alignment, wfe, atomic-persist]
dependency_graph:
  requires:
    - 04-01  # dedup.py, conflict_filter.py, regime_detector.py
  provides:
    - ranker.py with REGIME_ALIGNMENT_MAP and rank_signals()
    - quota.py with apply_quota()
    - runner.py with PipelineRunner.run() and _persist()
  affects:
    - Phase 6 risk gates (consumes ApprovedSignalORM rows)
    - Phase 7 execution engine (consumes ApprovedSignalORM rows)
    - APScheduler job (calls PipelineRunner.run() every 15 min)
tech_stack:
  added: []
  patterns:
    - asyncio.gather for parallel DB fetches (WFE per strategy)
    - async with session.begin() for atomic multi-table transaction
    - session.flush() before FK-dependent inserts (candidate → approved)
    - structlog structured events with pipeline.* key namespace
key_files:
  created:
    - src/pipeline/ranker.py
    - src/pipeline/quota.py
    - src/pipeline/runner.py
  modified: []
decisions:
  - "D-02 applied: WFE defaults to 0.5 when no active optimizer_results row — logged at pipeline.wfe_fallback"
  - "D-03 applied: REGIME_ALIGNMENT_MAP hardcoded as module-level constant in ranker.py"
  - "D-04 applied: single session.begin() transaction writes RegimeORM + all CandidateORM + all ApprovedORM"
  - "asyncio.gather used for parallel WFE fetches — one DB query per unique strategy, not per signal"
metrics:
  duration: "~15 minutes"
  completed: "2026-04-09"
  tasks_completed: 2
  tasks_total: 2
  files_created: 3
  files_modified: 0
---

# Phase 4 Plan 2: Signal Ranker, Quota Gate, and PipelineRunner Summary

Composite ranking formula (confidence*0.40 + rr_norm*0.30 + wfe*0.20 + regime_alignment*0.10) with hardcoded strategy-regime alignment map and 0.5 WFE fallback, quota gate enforcing MAX_SIGNALS_PER_DAY=5 via UTC day COUNT, and PipelineRunner orchestrating all 5 pipeline steps into a single atomic DB transaction.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Ranker and Quota Gate | 3c06458 | src/pipeline/ranker.py, src/pipeline/quota.py |
| 2 | PipelineRunner Coordinator | becc7db | src/pipeline/runner.py |

## What Was Built

### Task 1: Ranker and Quota Gate

**`src/pipeline/ranker.py`**
- `REGIME_ALIGNMENT_MAP` module-level constant with all 4 strategies and their aligned regime sets (D-03):
  - `liquidity_sweep` → `{"RANGING"}`
  - `trend_continuation` → `{"TRENDING_UP", "TRENDING_DOWN"}`
  - `breakout_expansion` → `{"TRENDING_UP", "TRENDING_DOWN", "HIGH_VOL"}`
  - `ema_momentum` → `{"TRENDING_UP", "TRENDING_DOWN"}`
- `_fetch_strategy_wfe()`: async helper querying `optimizer_results` for most recent active WFE; falls back to 0.5 with `log.info("pipeline.wfe_fallback", ...)` per D-02
- `rank_signals()`: async function computing composite score, uses `asyncio.gather()` for parallel WFE fetches across unique strategies, guards rr_norm division-by-zero, returns sorted `(CandidateSignal, float)` list descending by score

**`src/pipeline/quota.py`**
- `_count_today_approvals()`: async helper counting `approved_signals` rows for today UTC via `func.count()` + `func.date()`
- `apply_quota()`: enforces `max_per_day` (default 5) slots remaining after existing approvals; logs `pipeline.quota_enforced` when signals are rejected; returns `(approved_ranked, quota_rejected)` tuple

### Task 2: PipelineRunner Coordinator

**`src/pipeline/runner.py`**
- `PipelineRunner.run()`: async method accepting `list[CandidateSignal]` + `h1_candles`; orchestrates all 5 steps in sequence; builds `status_map` tracking DEDUPED/REJECTED/APPROVED per signal id; returns `list[ApprovedSignalORM]`
- `PipelineRunner._persist()`: single `async with session.begin()` transaction (T-04-05 mitigation) writing:
  1. `MarketRegimeORM` row
  2. All `CandidateSignalORM` rows with final statuses (complete audit trail — T-04-07 mitigation)
  3. `await session.flush()` to get DB-generated UUIDs
  4. All `ApprovedSignalORM` rows with FK to flushed candidate IDs
  5. Second `await session.flush()` for approved rows

## Verification Results

```
python -c "from src.pipeline.runner import PipelineRunner; from src.pipeline.ranker import REGIME_ALIGNMENT_MAP; from src.pipeline.quota import apply_quota; print('OK')"
# → OK

python -c "from src.pipeline.runner import PipelineRunner; from src.pipeline.ranker import REGIME_ALIGNMENT_MAP; print(REGIME_ALIGNMENT_MAP)"
# → {'liquidity_sweep': {'RANGING'}, 'trend_continuation': {'TRENDING_DOWN', 'TRENDING_UP'}, ...}

grep -c "0.40\|0.30\|0.20\|0.10" src/pipeline/ranker.py
# → 10 (all 4 weights present multiple times in docstring + formula)

grep -n "session.begin\|session.flush" src/pipeline/runner.py
# → 144: async with session.begin():
# → 175: await session.flush()
# → 190: await session.flush()
```

## Deviations from Plan

None — plan executed exactly as written.

## Threat Surface Scan

No new network endpoints, auth paths, or file access patterns introduced. All trust boundaries are internal (Pydantic → ORM conversion, COUNT query with no user-supplied inputs). Threat mitigations T-04-05, T-04-07, T-04-09 are implemented as planned.

## Self-Check: PASSED

Files created:
- FOUND: src/pipeline/ranker.py
- FOUND: src/pipeline/quota.py
- FOUND: src/pipeline/runner.py

Commits verified:
- FOUND: 3c06458 (feat(04-02): add signal ranker and quota gate)
- FOUND: becc7db (feat(04-02): add PipelineRunner coordinator)
