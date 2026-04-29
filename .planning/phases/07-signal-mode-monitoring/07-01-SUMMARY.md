---
phase: 07-signal-mode-monitoring
plan: 01
subsystem: database
tags: [alembic, sqlalchemy, orm, postgres, testing]
requires:
  - phase: 06-risk-management
    provides: Risk gates and breaker hooks that Phase 7 monitoring will consume
provides:
  - Phase 7 schema migration 0003
  - TradeORM trailing stop storage
  - StrategyStatsORM lifetime performance table
  - Execution and monitoring test scaffolds
affects: [phase-07-signal-mode-monitoring, execution, monitoring, dashboard]
tech-stack:
  added: []
  patterns: [Alembic additive migration, SQLAlchemy ORM model, pytest scaffold]
key-files:
  created:
    - alembic/versions/0003_phase7_signal_mode.py
    - src/models/strategy_stats.py
    - tests/test_execution/__init__.py
    - tests/test_execution/test_scaffold.py
    - tests/test_monitoring/conftest.py
  modified:
    - src/models/trade.py
    - src/models/__init__.py
key-decisions:
  - "Import StrategyStatsORM from src.models.__init__ so Alembic metadata discovery sees the new table."
  - "Added a tiny execution scaffold test because pytest returns exit code 5 for an empty collect-only directory."
patterns-established:
  - "Phase 7 DB changes are additive and chained from Alembic revision 0002."
  - "Monitoring tests get function-scoped FakeAsyncRedis and alert-hook reset fixtures."
requirements-completed: [SIG-02, SIG-03]
duration: 18min
completed: 2026-04-29
---

# Plan 07-01 Summary - Phase 7 Schema Foundation

**Trailing-stop persistence and per-strategy statistics schema are ready for Phase 7 execution and monitoring.**

## Performance

- **Duration:** 18 min
- **Started:** 2026-04-29T08:24:00Z
- **Completed:** 2026-04-29T08:42:46Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments

- Added Alembic revision `0003` chained to `0002`.
- Added nullable `TradeORM.trailing_stop_price` as `Numeric(12, 5)`.
- Added `StrategyStatsORM` with all D-16 fields for cumulative strategy performance.
- Added execution and monitoring pytest scaffolds for upcoming Telegram/executor/dashboard tests.
- Imported `StrategyStatsORM` in `src.models.__init__` for metadata discovery.

## Files Created/Modified

- `alembic/versions/0003_phase7_signal_mode.py` - Adds `trailing_stop_price` and creates `strategy_stats`.
- `src/models/trade.py` - Adds nullable trailing stop column.
- `src/models/strategy_stats.py` - New ORM model for per-strategy lifetime stats.
- `src/models/__init__.py` - Exposes `StrategyStatsORM` for Alembic/model discovery.
- `tests/test_execution/__init__.py` - Execution test package marker.
- `tests/test_execution/test_scaffold.py` - Minimal collection test until execution tests land.
- `tests/test_monitoring/conftest.py` - FakeRedis and alert-hook reset fixtures.

## Deviations from Plan

One small scaffold deviation:

- **Issue:** `pytest tests/test_execution/ --collect-only -q` exits with code 5 when the directory has no test files, even if `__init__.py` exists.
- **Fix:** Added `tests/test_execution/test_scaffold.py` with one neutral passing test.
- **Impact:** Keeps the package collectable now; later Phase 7 execution tests can coexist with it.

## Verification

Commands run:

```bash
grep -c "trailing_stop_price" src/models/trade.py
grep -c "class StrategyStatsORM" src/models/strategy_stats.py
grep -c "down_revision = \"0002\"" alembic/versions/0003_phase7_signal_mode.py
./.venv/bin/python -c "from src.models.strategy_stats import StrategyStatsORM; from src.models.trade import TradeORM; import src.models as models; print(StrategyStatsORM.__tablename__, hasattr(TradeORM, 'trailing_stop_price'), hasattr(models, 'StrategyStatsORM'))"
./.venv/bin/pytest tests/test_execution/ --collect-only -q
./.venv/bin/pytest tests/test_execution/ tests/test_monitoring/test_health_risk.py -q
./.venv/bin/alembic heads
./.venv/bin/alembic history --verbose
./.venv/bin/pytest -q
```

Results:

- `trailing_stop_price` grep: 1
- `StrategyStatsORM` grep: 1
- `down_revision = "0002"` grep: 1
- Import smoke: `strategy_stats True True`
- Execution collect-only: 1 test collected
- Targeted execution/monitoring tests: 4 passed
- Alembic head: `0003`
- Full suite: 256 passed

## Issues Encountered

- Empty pytest package collect-only behavior required the scaffold test noted above.

## User Setup Required

None.

## Next Phase Readiness

Plan `07-02` can now build `SignalSender` and `TelegramBot` against a stable Phase 7 schema foundation. Later plans can rely on `TradeORM.trailing_stop_price` and `StrategyStatsORM` existing.

---
*Phase: 07-signal-mode-monitoring*
*Completed: 2026-04-29*
