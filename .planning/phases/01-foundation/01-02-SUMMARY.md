---
phase: 01-foundation
plan: 02
subsystem: database
tags: [sqlalchemy, alembic, orm, postgres, async]
dependency_graph:
  requires: ["01-01"]
  provides: ["database-layer", "orm-models", "alembic-migration"]
  affects: ["02-01", "02-02", "03-01"]
tech_stack:
  added: ["SQLAlchemy 2.0 async", "asyncpg", "Alembic migrations"]
  patterns: ["mapped_column ORM", "DeclarativeBase", "async_sessionmaker", "FastAPI dependency injection"]
key_files:
  created:
    - src/database.py
    - src/models/__init__.py
    - src/models/candle.py
    - src/models/signal.py
    - src/models/trade.py
    - src/models/optimizer_result.py
    - src/models/regime.py
    - alembic/versions/0001_initial_schema.py
  modified:
    - alembic/env.py
    - pyproject.toml
decisions:
  - "Used SQLAlchemy 2.0 mapped_column syntax exclusively — no legacy Column() usage"
  - "Migration written manually to match CLAUDE.md section 6 schema exactly rather than using autogenerate"
  - "Fixed pyproject.toml build-backend from setuptools.backends.legacy to setuptools.build_meta for Python 3.13 venv compatibility"
metrics:
  duration_seconds: 318
  completed_date: "2026-04-05"
  tasks_completed: 2
  files_created: 9
  files_modified: 2
---

# Phase 1 Plan 2: Async Database Layer and ORM Models Summary

SQLAlchemy 2.0 async engine with DeclarativeBase, all 5 ORM model files (6 tables), and initial Alembic migration creating the complete schema from CLAUDE.md section 6.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Async database engine and declarative Base | 62c6270 | src/database.py, src/models/__init__.py |
| 2 | All ORM models (5 files, 6 tables) and initial Alembic migration | 3f8f1b3 | src/models/*.py, alembic/env.py, alembic/versions/0001_initial_schema.py |

## What Was Built

### src/database.py
- `create_async_engine` with `pool_pre_ping=True`, `pool_size=10`, `max_overflow=20`
- `AsyncSessionLocal` via `async_sessionmaker` with `expire_on_commit=False`
- `Base(DeclarativeBase)` — shared declarative base for all ORM models
- `get_db()` — FastAPI dependency yielding an async session with commit/rollback handling

### ORM Models (SQLAlchemy 2.0 mapped_column syntax throughout)

| Model Class | Table | Key Features |
|-------------|-------|--------------|
| `Candle` | `candles` | BigSerial PK, UniqueConstraint(instrument, timeframe, timestamp), 2 indexes |
| `CandidateSignalORM` | `candidate_signals` | UUID PK, JSONB params_snapshot, status field, 2 indexes |
| `ApprovedSignalORM` | `approved_signals` | UUID PK, FK to candidate_signals |
| `TradeORM` | `trades` | UUID PK, FK to approved_signals, optional close_reason |
| `OptimizerResultORM` | `optimizer_results` | UUID PK, JSONB params, WFE field, partial index on is_active |
| `MarketRegimeORM` | `market_regimes` | UUID PK, regime/atr/adx fields |

### alembic/versions/0001_initial_schema.py
- Creates all 6 tables in dependency order (candles → candidate_signals → approved_signals → trades → optimizer_results → market_regimes)
- All indexes from CLAUDE.md section 6 including partial index `WHERE is_active = TRUE`
- `downgrade()` drops tables in reverse order respecting FK constraints

### alembic/env.py
- Updated `target_metadata = None` → `target_metadata = Base.metadata`
- Imports `from src.models import Base` for autogenerate metadata discovery

## Verification

```
python -c "from src.models import Base; print(list(Base.metadata.tables.keys()))"
# Output: ['candles', 'candidate_signals', 'approved_signals', 'trades', 'optimizer_results', 'market_regimes']
```

All 6 tables confirmed in `Base.metadata.tables`. No legacy `Column()` usage — `mapped_column` throughout.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed pyproject.toml build-backend incompatibility with Python 3.13 venv**
- **Found during:** Task 1 — pip install failed with `Cannot import 'setuptools.backends.legacy'`
- **Issue:** `setuptools.backends.legacy:build` backend is not available in the Python 3.13 venv's bundled pip/setuptools
- **Fix:** Changed build-backend to `setuptools.build_meta` (standard stable backend)
- **Files modified:** pyproject.toml
- **Commit:** 62c6270

## Known Stubs

None — all model columns are fully defined per CLAUDE.md section 6 schema. No placeholder values.

## Threat Flags

None — no new network endpoints or auth paths introduced. All changes are ORM definitions and migration DDL within the existing postgres trust boundary documented in the plan's threat model.

## Self-Check: PASSED

- [x] src/database.py exists: FOUND
- [x] src/models/__init__.py exists: FOUND
- [x] src/models/candle.py exists: FOUND
- [x] src/models/signal.py exists: FOUND
- [x] src/models/trade.py exists: FOUND
- [x] src/models/optimizer_result.py exists: FOUND
- [x] src/models/regime.py exists: FOUND
- [x] alembic/versions/0001_initial_schema.py exists: FOUND
- [x] Commit 62c6270 exists: FOUND
- [x] Commit 3f8f1b3 exists: FOUND
- [x] Base.metadata.tables contains 6 tables: VERIFIED
