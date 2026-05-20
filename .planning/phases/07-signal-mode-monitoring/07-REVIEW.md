---
phase: 07-signal-mode-monitoring
reviewed: 2026-05-03T10:00:00Z
depth: standard
files_reviewed: 26
files_reviewed_list:
  - alembic/versions/0003_phase7_signal_mode.py
  - pyproject.toml
  - src/execution/__init__.py
  - src/execution/executor.py
  - src/execution/signal_sender.py
  - src/main.py
  - src/models/__init__.py
  - src/models/strategy_stats.py
  - src/models/trade.py
  - src/monitoring/dashboard.py
  - src/monitoring/health.py
  - src/monitoring/notification_adapter.py
  - src/pipeline/runner.py
  - src/risk/breaker.py
  - src/scheduler/jobs.py
  - src/templates/dashboard.html
  - tests/test_execution/__init__.py
  - tests/test_execution/test_executor.py
  - tests/test_execution/test_scaffold.py
  - tests/test_execution/test_signal_sender.py
  - tests/test_monitoring/conftest.py
  - tests/test_monitoring/test_dashboard.py
  - tests/test_monitoring/test_monitor_trades.py
  - tests/test_monitoring/test_strategy_stats.py
  - tests/test_monitoring/test_notification_adapter.py
  - tests/test_pipeline/test_runner_risk_step.py
findings:
  critical: 2
  warning: 8
  info: 4
  total: 14
status: issues_found
---

# Phase 7: Code Review Report

**Reviewed:** 2026-05-03T10:00:00Z
**Depth:** standard
**Files Reviewed:** 26
**Status:** issues_found

## Summary

This review covers Phase 7 (signal-mode-monitoring) of the 0rum trading bot. The codebase introduces signal-mode execution (External notification channel notifications), a monitoring dashboard, trade lifecycle management with trailing stops, circuit breaker state machine, strategy statistics, and scheduler jobs. Overall the code is well-structured with consistent patterns, but two critical issues exist: a detached-ORM-instance error that will silently prevent all trade lifecycle processing in production, and an incorrect blended P&L calculation for SL-closed trades that never reached TP1. Several warnings around security (XSS in dashboard), resource management (unnecessary Redis connections), and fragile code patterns are also identified.

## Critical Issues

### CR-01: DetachedInstanceError when accessing ORM attributes after session close

**File:** `src/scheduler/jobs.py:279`
**File:** `src/scheduler/jobs.py:248`

**Issue:** `monitor_trades()` (line 197) opens an `AsyncSessionLocal` context, fetches `TradeORM` objects, then closes the session at line 248 (`async with` block exit). After that, `_process_trade()` accesses attributes (`trade.direction`, `trade.entry_price`, `trade.sl_price`, `trade.status`, etc.) on the now-detached and expired ORM instances. SQLAlchemy's `AsyncSession.close()` calls `Session._close_impl()` which calls `expire_all()`, marking all tracked objects as expired. Accessing any expired attribute on a detached instance raises `DetachedInstanceError`.

This affects all attribute accesses in `_process_trade` starting at line 279:
- `direction = trade.direction` (line 279)
- `entry = Decimal(str(trade.entry_price))` (line 280)
- `sl = Decimal(str(trade.sl_price))` (line 281)
- `tp1 = Decimal(str(trade.tp1_price))` (line 282)
- `tp2 = Decimal(str(trade.tp2_price))` (line 283)
- `trade.status` (line 304)
- `trade.trailing_stop_price` (line 294, 344, 352)

The top-level `try/except` in `monitor_trades()` (line 265) will catch the exception and log `jobs.monitor_trades.failed`, but the trade will remain OPEN in the database. This recurrs every 15-minute cycle with the same outcome -- trades are never closed in production.

The tests use `MagicMock` for trade objects, so this bug is invisible to the test suite.

**Fix:** Restructure `monitor_trades()` to either:
1. Keep the session open while processing trades (process within the `async with session:` block)
2. Extract all needed scalar values into plain Python types before the session closes, rather than passing ORM instances
3. Re-query each trade by ID inside `_process_trade` within its own session

Option 2 (least invasive):
```python
# After fetching but before session closes:
open_trade_data = []
for trade_row, strategy_name in open_trades:
    open_trade_data.append({
        "id": trade_row.id,
        "direction": trade_row.direction,
        "entry_price": trade_row.entry_price,
        "sl_price": trade_row.sl_price,
        "tp1_price": trade_row.tp1_price,
        "tp2_price": trade_row.tp2_price,
        "trailing_stop_price": trade_row.trailing_stop_price,
        "status": trade_row.status,
        "strategy_name": strategy_name,
    })

# After session closes, process using plain dicts
for td in open_trade_data:
    await _process_trade(td, ...)
```

### CR-02: Blended P&L computation incorrect for SL closes on OPEN trades

**File:** `src/scheduler/jobs.py:385-387`

**Issue:** `_close_trade()` always computes blended P&L using `0.5 * tp1_pnl + 0.5 * final_pnl`, regardless of whether TP1 was actually hit. For an OPEN trade that hits SL directly (without ever reaching TP1), the formula incorrectly assumes half the position was filled at TP1:

```python
tp1_pnl = (tp1_price - entry_price) / entry_price * direction_sign
final_pnl = (exit_price - entry_price) / entry_price * direction_sign
blended_pnl_pct = Decimal("0.5") * tp1_pnl + Decimal("0.5") * final_pnl
```

For example, with entry=100, tp1=110, sl=90:
- tp1_pnl = (110-100)/100 = +10.0%
- final_pnl = (90-100)/100 = -10.0%
- blended = 0.5 * 0.10 + 0.5 * (-0.10) = 0.0%

The trade lost 10% but reports 0.0% P&L. This overstates performance for all SL-closed trades that never touched TP1.

This path is taken at line 309:
```python
if sl_hit:
    await _close_trade(trade, strategy_name, "SL", sl, entry, tp1, direction_sign)
```

**Fix:** For SL closes on OPEN trades (where TP1 was never reached), the blended formula should not apply. The P&L should be the full position loss:
```python
if close_reason == "SL" and pnl_blend:  # only blend for TP1_HIT-state closes
    blended_pnl_pct = Decimal("0.5") * tp1_pnl + Decimal("0.5") * final_pnl
else:
    blended_pnl_pct = final_pnl
```

Or track whether TP1 was ever hit and conditionally blend.

## Warnings

### WR-01: Dashboard HTML template vulnerable to XSS via innerHTML

**File:** `src/templates/dashboard.html:454-469`

**Issue:** Trade and signal data is concatenated directly into `innerHTML` using string interpolation without escaping. Fields such as `t.strategy`, `t.direction`, `sig.strategy`, and `sig.execution_status` are inserted via `html += '<td>' + t.strategy + '</td>'`. While current values come from enums, any future extension that allows user-facing text or free-form fields would create a client-side XSS vector.

**Fix:** Use `textContent` for all data values, or use `document.createElement`/`appendChild` instead of `innerHTML` string building. At minimum, escape HTML entities in strings before interpolation. Example pattern:
```javascript
function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}
```

### WR-02: Daily dashboard P&L sums pnl_pct which is mathematically meaningless

**File:** `src/monitoring/dashboard.py:102-108`

**Issue:** Daily P&L is computed as `SELECT SUM(TradeORM.pnl_pct) WHERE ...`. The `pnl_pct` field is a per-trade percentage return. Summing percentages from different trades (with potentially different position sizes) does not yield a meaningful portfolio return. The `TradeORM.pnl` field (absolute P&L) is never populated in `_close_trade()` (jobs.py line 396: only `pnl_pct` is set), so there is no absolute value to sum either.

**Fix:** Populate `TradeORM.pnl` in `_close_trade()` (using position size and entry price to compute dollar P&L), then sum `pnl` in the dashboard query. Alternatively, if only percentage metrics are desired, compute a time-weighted or size-weighted return.

### WR-03: BreakerManager is never wired as a singleton -- new Redis connection per invocation

**File:** `src/main.py:71`
**File:** `src/scheduler/jobs.py:31`

**Issue:** `_set_monitor_services()` is called with `breaker_manager=None`:
```python
_set_monitor_services(notification_adapter=notification_adapter_inst, breaker_manager=None)
```
This means `_breaker_manager` stays `None` throughout the application lifetime. Every call in `_close_trade()` (line 458) and `daily_summary()` (line 556) falls through to `BreakerManager()` which opens a new Redis connection. Over time this leaks connections.

**Fix:** Wire a shared `BreakerManager` instance, or make `BreakerManager` accept an existing Redis client from a connection pool.

### WR-04: Template directory path is relative -- breaks if CWD differs

**File:** `src/monitoring/dashboard.py:32`

**Issue:** `Jinja2Templates(directory="src/templates")` uses a relative path that depends on the current working directory. Standard deployment via `uvicorn src.main:app` from the project root works, but any deployment that changes CWD (e.g., systemd service unit with `WorkingDirectory=/opt/0rum/app/src`, Docker ENTRYPOINT with `WORKDIR /app`) will fail with `TemplateNotFound`.

**Fix:** Use an absolute path derived from `__file__`:
```python
import os
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)
```

### WR-05: Two independent Redis connections per /api/dashboard request and per /health check

**File:** `src/monitoring/dashboard.py:65,89`
**File:** `src/monitoring/health.py:48,63`

**Issue:** Each request to `/api/dashboard` creates two Redis clients (one for ping at line 65, one for circuit breaker at line 89). Similarly `/health` creates two Redis clients (lines 48 and 63). Each client establishes a new TCP connection. This multiplies Redis connections by request volume.

**Fix:** Reuse the same Redis client instance, or pass it as a shared dependency. Alternatively, open one connection for all Redis operations in a single request.

### WR-06: BreakerManager.decode_responses requirement is undocumented at call sites

**File:** `src/risk/breaker.py:28-38`

**Issue:** `BreakerManager.__init__` warns that `decode_responses=True` is REQUIRED when a Redis client is passed in, but doesn't enforce this. If any future caller passes a Redis client without `decode_responses=True`, `datetime.fromisoformat()` will silently receive `bytes` and raise `TypeError`. The constructor should normalize the client to enforce this constraint rather than relying on documentation.

**Fix:** In `BreakerManager.__init__`, wrap or check the passed Redis client:
```python
if redis is not None and not redis.connection_pool.connection_kwargs.get("decode_responses"):
    log.warning("risk.breaker.decode_responses_missing")
```

### WR-07: TradeORM.opened_at lacks explicit DateTime(timezone=True) declaration

**File:** `src/models/trade.py:43`

**Issue:** `TradeORM.opened_at` uses `mapped_column(nullable=False, server_default="NOW()")` without an explicit `DateTime(timezone=True)` type declaration. This is inconsistent with `StrategyStatsORM.updated_at` (strategy_stats.py:46) which explicitly declares `DateTime(timezone=True)`. While PostgreSQL's `NOW()` returns `timestamptz`, the missing type declaration means SQLAlchemy may use a bare `DateTime` type without timezone support, potentially causing timezone-naive datetime comparisons in Python code.

**Fix:** Add explicit type annotation:
```python
opened_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), nullable=False, server_default="NOW()"
)
```

### WR-08: Redundant import inside _refresh_timeframe

**File:** `src/scheduler/jobs.py:70-71`

**Issue:** `from datetime import datetime, timezone` is imported inside `_refresh_timeframe()` even though these modules are already imported at the top of the file (lines 4-5). This is dead code that will confuse readers and may mask a missing top-level import if the inner import is used as a crutch.

**Fix:** Remove the redundant inner import -- the top-level imports already cover it.

## Info

### IN-01: Strategy stats tests reimplement production logic (tautological test)

**File:** `tests/test_monitoring/test_strategy_stats.py`

**Issue:** The `_compute_stats()` helper reimplements the same mathematical logic as `_close_trade()` in jobs.py. The test then asserts that `1+1=2`. This validates the test helper's own arithmetic but does not test the production code path. Any bug in the spec would be reproduced identically in both the test and the production code. These tests provide no regression coverage for the actual `_close_trade()` function.

**Suggestion:** Extract the stats computation into a pure function that both `_close_trade` and the test import, or refactor these to integration-test the actual DB upsert logic with a real database.

### IN-02: Fragile assertion in test_executor.py for size_lots parameter

**File:** `tests/test_execution/test_executor.py:82`

**Issue:** The assertion uses an `or` fallback pattern to check that `size_lots` was passed correctly:
```python
assert kwargs.get("size_lots") == size or sender.send_signal.call_args[0][1] == size
```
Since the production code passes `size_lots` as a keyword argument, `kwargs` is populated and the first branch succeeds. But the fallback branch `sender.send_signal.call_args[0][1]` would raise `IndexError` if reached (empty `args` tuple). The duplication of logical paths adds maintenance cost with no safety benefit.

**Suggestion:** Simplify to a single assertion:
```python
assert kwargs.get("size_lots") == size
```

### IN-03: daily_summary breakdown omits CIRCUIT_BREAKER and MANUAL close reasons

**File:** `src/scheduler/jobs.py:560-569`

**Issue:** The daily summary message shows TP1, TP2, SL, and TRAIL counts but does not display CIRCUIT_BREAKER or MANUAL close reasons. When these occur, the trade is counted in `trades_opened` (sum of all close reasons) but invisible in the per-type breakdown. This makes the summary misleading -- `trades_opened` may exceed the sum of displayed counts.

**Suggestion:** Add rows for CIRCUIT_BREAKER and MANUAL in the daily summary display, or exclude them from `trades_opened` if they are not meant to be shown.

### IN-04: get_settings() docstring claims caching but does not cache

**File:** `src/config.py:80-82**

**Issue:** Already documented in CLAUDE.md as a known gap. `get_settings()` creates a new `Settings()` instance on every call. `pydantic-settings` with `model_config["env_file"]` re-reads the `.env` file on each instantiation, which is wasteful. This is noted here for completeness as it affects all call sites reviewed.

---

_Reviewed: 2026-05-03T10:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
