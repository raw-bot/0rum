---
phase: 04-signal-pipeline
plan: "01"
subsystem: pipeline
tags: [regime-detection, dedup, conflict-filter, signal-pipeline]
dependency_graph:
  requires: [src/models/signal_data.py, src/models/candle.py]
  provides: [src/backtesting/regime_detector.py, src/pipeline/dedup.py, src/pipeline/conflict_filter.py]
  affects: [04-02-PLAN.md (PipelineRunner consumes these modules)]
tech_stack:
  added: [numpy, scipy.stats.percentileofscore]
  patterns: [pure in-memory functions, Wilder EMA smoothing, Pydantic return types]
key_files:
  created:
    - src/backtesting/__init__.py
    - src/backtesting/regime_detector.py
    - src/pipeline/__init__.py
    - src/pipeline/dedup.py
    - src/pipeline/conflict_filter.py
  modified: []
decisions:
  - "RegimeDetector is async (consistent with project async everywhere convention) even though computation is CPU-bound — no blocking I/O in this version"
  - "ATR percentile uses scipy.stats.percentileofscore for correctness; imported inline in _calculate_atr_percentile to keep top-level imports clean"
  - "Dedup survivors dict maps group_key → index in survivors list so in-place replacement is O(1) without rebuilding the list"
  - "conflict_filter keeps entire winning direction (all buys or all sells) rather than only the best signal — matches CLAUDE.md §10.2 intent: keep higher-confidence direction"
  - "Tie-breaking on BUY (>= comparison) is deterministic and logged via rejected_count for audit trail (T-04-04 mitigation)"
metrics:
  duration_minutes: 12
  completed_date: "2026-04-09"
  tasks_completed: 2
  tasks_total: 2
  files_created: 5
  files_modified: 0
---

# Phase 04 Plan 01: Signal Pipeline Foundation Summary

**One-liner:** Pure in-memory regime detector (ADX+ATR percentile, 4 regimes) and two pipeline filters (60-min dedup, confidence-based conflict resolution) with structlog throughout and no DB I/O.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Regime Detector | 4c85c73 | src/backtesting/__init__.py, src/backtesting/regime_detector.py |
| 2 | Dedup Filter and Conflict Filter | 0555a14 | src/pipeline/__init__.py, src/pipeline/dedup.py, src/pipeline/conflict_filter.py |

## What Was Built

### RegimeDetector (src/backtesting/regime_detector.py)

Classifies current market regime from H1 candle ORM objects using:
- `_calculate_atr()`: True Range mean over last 14 bars
- `_calculate_adx()`: Wilder EMA-smoothed ADX(14) with ZeroDivisionError guard on flat candle data
- `_calculate_atr_percentile()`: scipy percentileofscore over 100 historical ATR windows
- `_calculate_ema()`: Standard EMA seeded with SMA(period)

Classification order (CLAUDE.md §11.4):
1. HIGH_VOL → `atr_pctile >= 0.90` (overrides all)
2. TRENDING_UP → `ADX > 25 AND EMA50 > EMA200`
3. TRENDING_DOWN → `ADX > 25 AND EMA50 < EMA200`
4. RANGING → default fallback

Returns `MarketRegime` Pydantic object. No DB writes.

### dedup_signals() (src/pipeline/dedup.py)

Removes duplicate signals within the 60-minute cooldown window (CLAUDE.md §10.1):
- Group key: `strategy:direction`
- Duplicate if entry prices within ±0.1% (`abs(a - b) / a < 0.001`)
- Most-recent survivor wins; earlier duplicate goes to `deduped` list
- No input mutation — callers track status externally

### filter_conflicts() (src/pipeline/conflict_filter.py)

Resolves BUY/SELL conflicts for XAUUSD (CLAUDE.md §10.2):
- Groups by Direction.BUY and Direction.SELL
- When conflict exists: entire winning direction is kept, entire losing direction rejected
- Higher confidence wins; tie-break favors BUY (`>=` comparison) for determinism
- Logs `rejected_count` for audit trail (T-04-04 mitigation)

## Deviations from Plan

### Auto-fixed Issues

None.

### Threat Model Mitigations Applied

**T-04-01 (Tampering):** Both `dedup_signals()` and `filter_conflicts()` return new lists without mutating input `CandidateSignal` objects.

**T-04-02 (DoS — ZeroDivisionError):** `_calculate_adx()` guards `di_sum == 0` with an explicit check returning 0.0, preventing crashes on flat candle data.

**T-04-04 (Conflict filter tie-break):** Uses `>=` on BUY confidence comparison for deterministic winner selection; `rejected_count` is always logged.

## Known Stubs

None — all modules are fully implemented.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes introduced. All modules are pure in-memory computation.

## Self-Check: PASSED

Files created:
- src/backtesting/__init__.py — FOUND
- src/backtesting/regime_detector.py — FOUND
- src/pipeline/__init__.py — FOUND
- src/pipeline/dedup.py — FOUND
- src/pipeline/conflict_filter.py — FOUND

Commits verified:
- 4c85c73 — FOUND (feat(04-01): implement RegimeDetector)
- 0555a14 — FOUND (feat(04-01): implement dedup_signals() and filter_conflicts())

Import verification:
```
python -c "from src.backtesting.regime_detector import RegimeDetector; from src.pipeline.dedup import dedup_signals; from src.pipeline.conflict_filter import filter_conflicts; print('all imports OK')"
→ all imports OK
```
