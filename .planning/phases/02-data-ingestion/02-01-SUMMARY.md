---
phase: 02-data-ingestion
plan: "02-01"
subsystem: ingestion
tags: [oanda, httpx, postgresql, sqlalchemy, structlog, asyncio, candles]

# Dependency graph
requires:
  - phase: 01-foundation
    provides: Candle ORM model, AsyncSessionLocal, Settings with oanda_api_key/oanda_account_id/oanda_api_url
provides:
  - OandaClient async HTTP client for OANDA v20 candle endpoint
  - CandleFetcher with fetch_and_store (ON CONFLICT DO NOTHING upsert), backfill_timeframe, backfill_all, prune_incomplete
affects:
  - 02-02-gap-detector (uses CandleFetcher.fetch_and_store)
  - 02-03-scheduler (calls CandleFetcher methods on schedule)
  - 03-strategy-engine (depends on candle data being present in DB)

# Tech tracking
tech-stack:
  added: [httpx (async HTTP client), sqlalchemy postgresql dialect (pg_insert)]
  patterns:
    - "OandaClient stores auth only in self.headers — never referenced after __init__, never logged"
    - "pg_insert(...).on_conflict_do_nothing() for idempotent candle upserts"
    - "Pagination loop guarded by MAX_PAGINATION_ITERS=200 and empty-response break"
    - "Malformed candles skipped via _parse_candle() returning None — never crash on bad data"

key-files:
  created:
    - src/ingestion/__init__.py
    - src/ingestion/oanda_client.py
    - src/ingestion/candle_fetcher.py
  modified: []

key-decisions:
  - "D1 granularity mapped to 'D' when calling OANDA — D1 is internal name, OANDA uses 'D'"
  - "count and from_time are mutually exclusive in OANDA API — count is deleted from params when from_time is set"
  - "backfill_all() is designed to run as asyncio.create_task() so it does not block FastAPI startup"
  - "MAX_PAGINATION_ITERS=200 is a hard safety cap (6 months of M15 needs ~36 pages max)"

patterns-established:
  - "Auth pattern: credentials stored in headers dict in __init__, never passed to log calls"
  - "Error logging pattern: HTTPStatusError logs only status_code and url — never response body or request headers"
  - "Upsert pattern: pg_insert().on_conflict_do_nothing(index_elements=[...]) for all candle inserts"
  - "Pagination pattern: advance from_time past last candle timestamp, break on empty or <500 results"

requirements-completed: [DATA-01]

# Metrics
duration: 2min
completed: 2026-04-06
---

# Phase 02 Plan 01: OANDA Client and Candle Fetcher Summary

**Async OANDA v20 candle client with httpx and PostgreSQL upsert storage via SQLAlchemy pg_insert ON CONFLICT DO NOTHING, plus 6-month paginated backfill across M15/H1/H4/D1 timeframes**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-04-06T00:47:54Z
- **Completed:** 2026-04-06T00:49:47Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- OandaClient with get_candles() wraps OANDA v20 `/v3/instruments/{instrument}/candles` using httpx.AsyncClient — D1 maps to "D", count dropped when from_time is set, price=MBA
- CandleFetcher.fetch_and_store() parses OANDA candle dicts, skips malformed records, upserts via pg_insert ON CONFLICT DO NOTHING on (instrument, timeframe, timestamp)
- CandleFetcher.backfill_timeframe() paginates 6 months backward from now, guarded by MAX_PAGINATION_ITERS=200 and empty-response break to prevent infinite loops
- CandleFetcher.prune_incomplete() deletes complete=False candles older than 24h per CLAUDE.md 8.3
- OANDA credentials never appear in any log call — auth key set once in headers dict in __init__

## Task Commits

Each task was committed atomically:

1. **Task 1: OANDA v20 async client** - `a4d70c1` (feat)
2. **Task 2: Candle fetcher with upsert storage and 6-month backfill** - `0312bb5` (feat)

## Files Created/Modified

- `src/ingestion/__init__.py` - Package marker (empty)
- `src/ingestion/oanda_client.py` - OandaClient: async OANDA v20 candle fetching with auth, error handling, D1→D mapping
- `src/ingestion/candle_fetcher.py` - CandleFetcher: fetch, parse, upsert, backfill all 4 timeframes, prune incomplete

## Decisions Made

- D1 is the internal timeframe name; OANDA API requires "D" — explicit mapping in get_candles()
- count and from_time are mutually exclusive in OANDA v20 — count is deleted from params dict when from_time is provided
- backfill_all() is designed to run via asyncio.create_task() so FastAPI startup is not blocked
- MAX_PAGINATION_ITERS=200 guards against infinite pagination (6 months M15 needs ~36 pages, 200 is a 5.5x safety margin)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None — comment text in oanda_client.py initially contained literal `e.response.text` in a warning comment, which matched the security grep check. Rephrased the comment to "response body or request headers" to avoid the false positive while preserving intent.

## User Setup Required

None - no external service configuration required. OANDA credentials are read from environment via Settings (OANDA_API_KEY, OANDA_ACCOUNT_ID, OANDA_API_URL).

## Next Phase Readiness

- OandaClient and CandleFetcher are ready for use by Plan 02-02 (gap detector) and Plan 02-03 (scheduler)
- backfill_all() is designed to be wired into FastAPI startup lifecycle as asyncio.create_task()
- No blockers — both modules import cleanly with correct virtualenv

---
*Phase: 02-data-ingestion*
*Completed: 2026-04-06*
