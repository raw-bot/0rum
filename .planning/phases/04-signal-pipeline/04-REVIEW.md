---
phase: 04-signal-pipeline
reviewed: 2026-04-09T00:00:00Z
depth: standard
files_reviewed: 16
files_reviewed_list:
  - src/backtesting/__init__.py
  - src/backtesting/regime_detector.py
  - src/pipeline/__init__.py
  - src/pipeline/dedup.py
  - src/pipeline/conflict_filter.py
  - src/pipeline/ranker.py
  - src/pipeline/quota.py
  - src/pipeline/runner.py
  - tests/test_pipeline/__init__.py
  - tests/test_pipeline/test_dedup.py
  - tests/test_pipeline/test_conflict_filter.py
  - tests/test_pipeline/test_ranker.py
  - tests/test_pipeline/test_quota.py
  - tests/test_pipeline/test_regime_detector.py
  - tests/test_pipeline/test_runner.py
  - src/scheduler/jobs.py
findings:
  critical: 0
  warning: 4
  info: 4
  total: 8
status: issues_found
---

# Phase 4: Code Review Report

**Reviewed:** 2026-04-09T00:00:00Z
**Depth:** standard
**Files Reviewed:** 16
**Status:** issues_found

## Summary

The signal pipeline implementation is well-structured and largely correct. The dedup, conflict filter, ranker, quota, and PipelineRunner all follow the D-01 through D-05 architectural decisions documented in CLAUDE.md. The `session.begin() + session.flush()` persist pattern is correctly implemented. The REGIME_ALIGNMENT_MAP is properly hardcoded as a module-level constant. The WFE fallback to 0.5 is correctly logged at `pipeline.wfe_fallback` per D-02.

Four warnings require attention before shipping:

1. A dedup logic bug where a non-duplicate signal (price > 0.1% apart) overwrites the `seen` dict pointer, causing subsequent signals to only dedup against the most recent survivor rather than all survivors with the same group key.
2. The UTC date filter in `quota.py` uses `func.date(col)` without an explicit `AT TIME ZONE 'UTC'` cast, which can silently use the database session timezone.
3. A falsy check in `regime_detector.py` logging causes ADX of 0.0 to be logged as `None`.
4. An unnecessary DB hit in `quota.py` — `_count_today_approvals()` is always called even when `ranked_signals` is empty.

---

## Warnings

### WR-01: Dedup seen-pointer overwrite drops multi-survivor dedup coverage

**File:** `src/pipeline/dedup.py:74`
**Issue:** When two signals share the same `group_key` (same strategy + direction) but their entry prices are more than 0.1% apart, neither is removed (correct). However, the `seen` dict is updated to point at the newer signal's index (`seen[group_key] = len(survivors)`), overwriting the pointer to the earlier signal. If a third signal then arrives with the same group_key and an entry price within 0.1% of the FIRST survivor (but not the second), the dedup check on line 57-71 will compare only against the second survivor and will miss the dedup. This means signals that should be deduplicated can survive when there are three or more signals in the same group.

**Affected scenario:** Strategy A, BUY, prices: 2340.0, 2345.0 (distinct, both survive), then 2340.2 (should dedup against 2340.0, but `seen` now points to 2345.0, so it passes through as a third survivor).

**Fix:** Maintain a list of all survivor indices per group key, or change the seen dict to track all survivors per group rather than only the last one:

```python
# Option 1: track all survivors per group_key and check all of them
seen: dict[str, list[int]] = {}  # group_key → list of survivor indices

for signal in signals:
    group_key = f"{signal.strategy.value}:{signal.direction.value}"
    matched_idx = None
    if group_key in seen:
        for idx in seen[group_key]:
            existing = survivors[idx]
            if existing.entry_price > 0 and abs(signal.entry_price - existing.entry_price) / existing.entry_price < 0.001:
                matched_idx = idx
                break
    if matched_idx is not None:
        deduped.append(survivors[matched_idx])
        survivors[matched_idx] = signal
        log.info("pipeline.dedup", ...)
    else:
        seen.setdefault(group_key, []).append(len(survivors))
        survivors.append(signal)
```

---

### WR-02: Quota UTC date filter missing explicit timezone cast

**File:** `src/pipeline/quota.py:33`
**Issue:** The query uses `func.date(ApprovedSignalORM.created_at) == today` where `today` is `datetime.now(timezone.utc).date()`. In PostgreSQL, `date(timestamptz_col)` extracts the date using the current session timezone, which defaults to whatever `TimeZone` is set on the server or connection — not necessarily UTC. If the server timezone is not UTC (or if the connection timezone is changed), signals from 23:xx UTC could be counted in the next day or previous day, silently breaking the per-UTC-day quota invariant. The docstring on line 45 even states `DATE(created_at AT TIME ZONE 'UTC')` but the implementation does not apply the cast.

**Fix:**
```python
from sqlalchemy import cast, Date, func

stmt = (
    select(func.count())
    .select_from(ApprovedSignalORM)
    .where(
        cast(
            func.timezone("UTC", ApprovedSignalORM.created_at),
            Date,
        ) == today
    )
)
```
Or equivalently in raw SQL terms: `WHERE (created_at AT TIME ZONE 'UTC')::date = :today`.

---

### WR-03: ADX value of 0.0 logged as None due to falsy check

**File:** `src/backtesting/regime_detector.py:70`
**Issue:** The log call uses `round(adx_value, 4) if adx_value else None`. In Python, `0.0` is falsy, so when `_calculate_adx()` legitimately returns 0.0 (insufficient data path at line 124 or 183), the log entry emits `adx_value=None` instead of `adx_value=0.0`. This does not affect the actual returned `MarketRegime` object (line 77 receives the correct `adx_value`), but the structured log becomes misleading — `None` implies "not calculated" while `0.0` implies "flat market / insufficient data". The `MarketRegime` Pydantic model declares `adx_value: Optional[float] = None`, so downstream consumers that rely on `None` to mean "not computed" could misinterpret a computed 0.0 as missing.

**Fix:**
```python
# Line 70-71: use explicit None check, not truthiness
adx_value=round(adx_value, 4) if adx_value is not None else None,
```
Since `_calculate_adx` always returns a `float` (never `None`), the log should simply be:
```python
adx_value=round(adx_value, 4),
```

---

### WR-04: Unnecessary DB query when ranked_signals is empty in apply_quota

**File:** `src/pipeline/quota.py:59`
**Issue:** `_count_today_approvals()` is always awaited, including when `ranked_signals` is empty. This makes an unnecessary DB round-trip for every pipeline run that produces no signals after conflict filtering. The `PipelineRunner` calls `apply_quota(ranked, ...)` where `ranked` may be empty (e.g., all signals deduped or conflict-rejected). The quota gate has no effect on an empty list, so the DB query is wasted I/O.

**Fix:**
```python
async def apply_quota(
    ranked_signals: list[tuple[CandidateSignal, float]],
    max_per_day: int = 5,
) -> tuple[list[tuple[CandidateSignal, float]], list[CandidateSignal]]:
    if not ranked_signals:
        return [], []

    existing = await _count_today_approvals()
    ...
```

---

## Info

### IN-01: Unused import in jobs.py

**File:** `src/scheduler/jobs.py:3`
**Issue:** `import asyncio` is present at the top of the file but `asyncio` is never referenced anywhere in `jobs.py`. APScheduler and the async functions do not require an explicit `asyncio` import.

**Fix:** Remove `import asyncio` from line 3.

---

### IN-02: Deferred import of scipy inside method body

**File:** `src/backtesting/regime_detector.py:226`
**Issue:** `from scipy.stats import percentileofscore` is imported inside `_calculate_atr_percentile()` rather than at the module top level. Deferred imports hide dependencies, complicate static analysis, and add overhead on every call (Python caches the import after the first call, but the lookup still occurs). This also violates the project convention of having all imports at the top of each file.

**Fix:** Move to module-level imports alongside `numpy` and `structlog`:
```python
import numpy as np
import structlog
from scipy.stats import percentileofscore
```

---

### IN-03: Deferred import of datetime inside jobs.py function

**File:** `src/scheduler/jobs.py:41`
**Issue:** `from datetime import datetime, timezone` is imported inside `_refresh_timeframe()` rather than at the module top level. Same issue as IN-02 — hides dependency, inconsistent with project style.

**Fix:** Move to the top-level imports block in `jobs.py`.

---

### IN-04: Test coverage gap for dedup three-signal edge case

**File:** `tests/test_pipeline/test_dedup.py`
**Issue:** No test covers the scenario where three signals share the same group_key (strategy + direction) with varying entry prices. Specifically, the bug described in WR-01 (seen-pointer overwrite) is not exercised by any existing test. All current dedup tests use at most two signals per group_key.

**Fix:** Add a test:
```python
def test_dedup_three_signals_same_group_first_and_third_close():
    """Third signal within 0.1% of first but not second must be deduped."""
    sig1 = make_signal(entry_price=2340.0)  # survivor candidate
    sig2 = make_signal(entry_price=2345.0)  # distinct price — both survive
    sig3 = make_signal(entry_price=2340.2)  # within 0.1% of sig1, should dedup sig1
    survivors, deduped = dedup_signals([sig1, sig2, sig3])
    # sig3 should dedup sig1; sig2 is distinct
    assert len(survivors) == 2
    assert sig3 in survivors
    assert sig2 in survivors
    assert sig1 in deduped
```
Note: this test will currently FAIL, confirming the WR-01 bug is real.

---

_Reviewed: 2026-04-09T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
