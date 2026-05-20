---
phase: 02-data-ingestion
plan: "02-03"
subsystem: ingestion-tests
tags: [pytest, respx, aiosqlite, unit-tests, httpx-mock, sqlite-in-memory, asyncio]

# Dependency graph
requires:
  - phase: 02-data-ingestion
    plan: 02-01
    provides: RetiredProviderClient, CandleFetcher with _parse_candle(), backfill_timeframe()
  - phase: 02-data-ingestion
    plan: 02-02
    provides: GapDetector with find_gaps(), detect_and_fill()
provides:
  - Unit tests for RetiredProviderClient (5 tests, httpx mocked via respx)
  - Unit tests for CandleFetcher (4 tests, RetiredProviderClient mocked)
  - Unit tests for GapDetector (4 tests, in-memory SQLite via aiosqlite)
  - tests/conftest.py with env var setup for offline test collection
affects:
  - CI pipeline (13 tests pass without live OANDA_RETIRED or PostgreSQL)

# Tech tracking
tech-stack:
  added: [respx>=0.20.0 (httpx mock transport), aiosqlite>=0.19.0 (SQLite async driver)]
  patterns:
    - "respx.mock decorator intercepts all httpx.AsyncClient calls in RetiredProviderClient tests"
    - "GapDetector tests use raw SQLite DDL fixture — avoids PostgreSQL JSONB/gen_random_uuid incompatibilities"
    - "AsyncSessionLocal patched via pytest.MonkeyPatch.context() inside each GapDetector test"
    - "CandleFetcher.__new__(CandleFetcher) instantiates without __init__ to avoid DB engine creation"
    - "find_gaps uses delta > expected_delta * 2 — tests skip 2 candles to produce 3× interval gap"
    - "conftest.py sets env vars via os.environ.setdefault before any src.* imports"

key-files:
  created:
    - tests/__init__.py
    - tests/conftest.py
    - tests/test_ingestion/__init__.py
    - tests/test_ingestion/test_retired_provider_client.py
    - tests/test_ingestion/test_candle_fetcher.py
    - tests/test_ingestion/test_gap_detector.py
  modified:
    - pyproject.toml

key-decisions:
  - "Raw DDL used for SQLite fixture instead of ORM Base.metadata.create_all — other models (candidate_signals) use JSONB/gen_random_uuid that SQLite cannot compile"
  - "Gap detection threshold is delta > 2× interval (strictly greater) — tests must skip 2+ consecutive candles to trigger, not just 1"
  - "conftest.py uses os.environ.setdefault to provide dummy values for pydantic_settings — Settings() is called at module import time in src/database.py"
  - "GapDetector tests use now()-relative timestamps to fall within lookback_hours=48 window"

requirements-completed: [DATA-01, DATA-02]

# Metrics
duration: ~15min
completed: 2026-04-06
---

# Phase 02 Plan 03: Ingestion Unit Tests Summary

**13 unit tests across 3 files covering RetiredProviderClient (respx-mocked httpx), CandleFetcher (mocked RetiredProviderClient), and GapDetector (in-memory SQLite via aiosqlite) — all pass without live OANDA_RETIRED connection or PostgreSQL**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-04-06T03:00:00Z
- **Completed:** 2026-04-06T03:17:00Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- test_retired_provider_client.py (5 tests): candle list return, D1→D granularity mapping, from_time removes count param, HTTP 401 raises HTTPStatusError, empty response returns []
- test_candle_fetcher.py (4 tests): parse valid candle returns Candle ORM, missing mid returns None, missing timestamp returns None, backfill terminates immediately on empty API response
- test_gap_detector.py (4 tests): no gaps in consecutive sequence, gap detected when 2+ candles missing, detect_and_fill calls fetch_and_store once per gap, empty table returns []
- tests/conftest.py: sets required env vars via os.environ.setdefault before any src.* module collection
- pyproject.toml: added aiosqlite>=0.19.0 and respx>=0.20.0 to project dependencies

## Task Commits

Each task was committed atomically:

1. **Task 1: RetiredProviderClient and CandleFetcher unit tests** - `5417f15` (test)
2. **Task 2: GapDetector unit tests with in-memory fixture** - `3387c6d` (test)

## Files Created/Modified

- `tests/__init__.py` — package marker
- `tests/conftest.py` — env var setup for offline test collection (Rule 2: missing critical for test collection)
- `tests/test_ingestion/__init__.py` — package marker
- `tests/test_ingestion/test_retired_provider_client.py` — 5 RetiredProviderClient tests with respx mock
- `tests/test_ingestion/test_candle_fetcher.py` — 4 CandleFetcher tests with mocked RetiredProviderClient
- `tests/test_ingestion/test_gap_detector.py` — 4 GapDetector tests with SQLite in-memory fixture
- `pyproject.toml` — added aiosqlite>=0.19.0 and respx>=0.20.0

## Decisions Made

- Raw DDL used for SQLite fixture (not `Base.metadata.create_all`) — `candidate_signals` model uses JSONB and `gen_random_uuid()` server defaults that are incompatible with SQLite's type compiler
- `find_gaps` threshold is `delta > expected_delta * 2` (strictly greater than) — skipping 1 candle yields `delta == 2×interval` which is NOT flagged; tests skip 2 consecutive candles to produce `delta = 3×interval > 2×interval`
- `conftest.py` sets env vars before module imports — `src/database.py` calls `get_settings()` at module level and would fail collection without valid credentials
- Timestamps in GapDetector tests are `now() - 24h` relative, not hardcoded 2024 dates, to fall within the `lookback_hours=48` filter window

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added tests/conftest.py with env var setup**
- **Found during:** Task 1 collection
- **Issue:** `src/database.py` imports `get_settings()` at module level; no `.env` exists in worktree — collection failed with pydantic ValidationError
- **Fix:** Created `tests/conftest.py` with `os.environ.setdefault` for all required Settings fields
- **Files modified:** `tests/conftest.py`
- **Commit:** `5417f15`

**2. [Rule 1 - Bug] SQLite fixture uses raw DDL instead of ORM metadata**
- **Found during:** Task 2 execution
- **Issue:** `Base.metadata.create_all` tries to create all models including `candidate_signals` which uses JSONB — SQLite cannot compile `visit_JSONB`
- **Fix:** Replaced with raw SQLite-compatible DDL that creates only the `candles` table
- **Files modified:** `tests/test_ingestion/test_gap_detector.py`
- **Commit:** `3387c6d`

**3. [Rule 1 - Bug] ORM BigInteger autoincrement fails on SQLite INSERT**
- **Found during:** Task 2 execution (after JSONB fix)
- **Issue:** `Candle.__table__.create` succeeded but ORM INSERT with `BigInteger` + `server_default="NOW()"` fails on SQLite (`NOT NULL constraint failed: candles.id`)
- **Fix:** Replaced ORM `session.add_all()` with raw SQL inserts via `text()`, bypassing the autoincrement and server_default issues
- **Files modified:** `tests/test_ingestion/test_gap_detector.py`
- **Commit:** `3387c6d`

**4. [Rule 1 - Bug] Gap detection threshold requires >2× interval, not ≥2×**
- **Found during:** Task 2 test execution (test_find_gaps_detects_missing_candle returned 0 gaps)
- **Issue:** Plan's test indices [0,1,2,3,5,6] skip 1 candle producing delta=2×interval, but `find_gaps` uses `delta > expected_delta * 2` (strictly greater). 2×interval is NOT > 2×interval.
- **Fix:** Changed test indices to [0,1,2,3,6,7] — skipping 2 candles produces delta=3×interval which IS > 2×interval
- **Files modified:** `tests/test_ingestion/test_gap_detector.py`
- **Commit:** `3387c6d`

## Threat Flags

No new security-relevant surface introduced. Tests are offline-only: no network endpoints, no auth paths, no file access patterns, no schema changes at trust boundaries.

## Self-Check: PASSED

Files verified:
- tests/test_ingestion/__init__.py: FOUND
- tests/conftest.py: FOUND
- tests/test_ingestion/test_retired_provider_client.py: FOUND (5 test functions)
- tests/test_ingestion/test_candle_fetcher.py: FOUND (4 test functions)
- tests/test_ingestion/test_gap_detector.py: FOUND (4 test functions)
- pyproject.toml: aiosqlite and respx added: FOUND

Commits verified:
- 5417f15 (Task 1): FOUND
- 3387c6d (Task 2): FOUND

Test run: 13 passed, 0 failed — `pytest tests/test_ingestion/ -x -v`
