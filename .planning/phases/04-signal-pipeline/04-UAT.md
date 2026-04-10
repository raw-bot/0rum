---
status: complete
phase: 04-signal-pipeline
source: [04-01-SUMMARY.md, 04-02-SUMMARY.md, 04-03-SUMMARY.md]
started: "2026-04-10T00:00:00Z"
updated: "2026-04-10T00:00:00Z"
---

## Current Test

[testing complete]

## Tests

### 1. Cold Start Smoke Test
expected: Tear down and restart the stack from scratch (docker compose down -v && docker compose up -d). App container starts, Alembic migrations run, and the health endpoint responds (curl http://localhost:8000/health). No errors in logs. Scheduler boots and registers jobs.
result: skipped
reason: not run yet

### 2. Full Pipeline Test Suite — 27 tests pass
expected: Running `pytest tests/test_pipeline/ -v` from the project root produces 27 passed, 0 failed, 0 errors. All 6 test files are collected (test_dedup, test_conflict_filter, test_ranker, test_quota, test_regime_detector, test_runner).
result: pass

### 3. Regime Detector — 4 regimes classified correctly
expected: Running `pytest tests/test_pipeline/test_regime_detector.py -v` shows 5 tests passing, covering: HIGH_VOL override (ATR >= 90th percentile), TRENDING_UP (ADX > 25 + EMA50 > EMA200), TRENDING_DOWN (ADX > 25 + EMA50 < EMA200), RANGING (default fallback), and ATR=0.0 guard for flat candle data.
result: pass

### 4. Dedup Filter — 60-minute cooldown window
expected: Running `pytest tests/test_pipeline/test_dedup.py -v` shows 5 tests passing, covering: same-strategy dedup, direction exclusion (BUY vs SELL treated as separate), ±0.1% price threshold boundary, empty input, and different-strategy independence.
result: pass

### 5. Conflict Filter — BUY/SELL direction resolution
expected: Running `pytest tests/test_pipeline/test_conflict_filter.py -v` shows 5 tests passing, covering: BUY wins (higher confidence), SELL wins (higher confidence), same-direction all kept (no conflict), empty input, and tie → BUY preferred (deterministic).
result: pass

### 6. Signal Ranker — composite scoring formula
expected: Running `pytest tests/test_pipeline/test_ranker.py -v` shows 6 tests passing, covering: REGIME_ALIGNMENT_MAP constants (all 4 strategies mapped), sorted descending output by composite score, WFE fallback = 0.5 when no optimizer result, empty input returns [].
result: pass

### 7. Quota Gate — MAX_SIGNALS_PER_DAY=5 enforced
expected: Running `pytest tests/test_pipeline/test_quota.py -v` shows 4 tests passing, covering: all signals pass when 0 existing approvals today, partial slots (4 existing → 1 slot left), fully exhausted (5 existing → 0 slots, all rejected), empty input.
result: pass

### 8. PipelineRunner — end-to-end orchestration
expected: Running `pytest tests/test_pipeline/test_runner.py -v` shows 2 tests passing: empty candidates returns [] immediately, and conflict resolved with mocked regime/rank/quota/DB produces ApprovedSignalORM output.
result: pass

### 9. APScheduler — run_pipeline job registered at 15-min interval
expected: Running `python -c "from src.scheduler.jobs import create_scheduler; s = create_scheduler(); jobs = s.get_jobs(); ids = [j.id for j in jobs]; print(ids); assert 'run_pipeline' in ids, 'missing'; print('OK')"` prints a list containing `run_pipeline` and then `OK`, with no ImportError or AttributeError.
result: pass

## Summary

total: 9
passed: 8
issues: 0
pending: 0
skipped: 1
blocked: 0

## Gaps

[none]
