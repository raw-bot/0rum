---
status: partial
phase: 02-data-ingestion
source: [02-01-SUMMARY.md, 02-02-SUMMARY.md, 02-03-SUMMARY.md]
started: 2026-04-06T00:00:00Z
updated: 2026-04-07T00:00:00Z
---

## Current Test

[testing complete — legacy OANDA tests cancelled]

## Provider Realignment Note

Phase 2 delivery currently proves ingestion plumbing, not final XAUUSD data fidelity. Binance/CCXT PAXG/USDT remains a temporary proxy path; before Phase 5 optimizer/backtest work begins, the project must integrate and validate a real XAUUSD provider feed. Current target: IG demo → live.

## Tests

### 1. Cold Start Smoke Test
expected: Kill any running server/service. Clear ephemeral state (temp DBs, caches, lock files). Start the application from scratch (e.g. `uvicorn src.main:app`). Server boots without errors, migrations/backfill launch as background tasks without crashing, and a basic health check (`GET /health`) returns a live response.
result: pass
note: legacy broker API backfill skipped in test env — server did not crash, error was caught and logged via structured JSON logger

### 2. Unit Tests Pass (Offline)
expected: Running `pytest tests/test_ingestion/ -x -v` with no live broker connection or PostgreSQL passes all 13 tests. No failures, no collection errors.
result: skipped
reason: user moved to Binance

### 3. Broker Client Candle Fetch
expected: Calling `BrokerClient.get_candles(instrument="EUR_USD", granularity="H1", count=10)` returns a list of raw candle dicts. D1 granularity maps to "D" when sent to broker. Providing `from_time` removes `count` from the request params automatically.
result: cancelled
reason: "Broker migration - legacy OANDA"

### 4. Candle Upsert Storage
expected: `CandleFetcher.fetch_and_store(instrument="EUR_USD", timeframe="H1")` stores candles to the DB. Running it twice does not duplicate rows — the second call is a no-op (ON CONFLICT DO NOTHING). Malformed candles are skipped, not raised.
result: cancelled
reason: "Broker migration - legacy OANDA"

### 5. Backfill Across All Timeframes
expected: `CandleFetcher.backfill_all()` paginates 6 months of candle history for M15/H1/H4/D1. Completion logs for each timeframe appear in structured logs. No infinite loop — MAX_PAGINATION_ITERS=200 guards against pagination runaway.
result: cancelled
reason: "Broker migration - legacy OANDA"

### 6. Prune Incomplete Candles
expected: `CandleFetcher.prune_incomplete()` deletes rows where `complete=False` and timestamp is older than 24 hours. Rows with `complete=True` or timestamp within 24h are untouched.
result: cancelled
reason: "Broker migration - legacy OANDA"

### 7. Gap Detection
expected: `GapDetector.find_gaps(instrument="EUR_USD", timeframe="M15")` returns Gap objects for time ranges where 2+ consecutive candles are missing (delta > 2× expected interval). Consecutive candles with only 1 missing are not flagged. Weekend gaps (Friday close to Sunday open, ≤48h) are tolerated.
result: cancelled
reason: "Broker migration - legacy OANDA"

### 8. Gap Filling
expected: `GapDetector.detect_and_fill(instrument="EUR_USD", timeframe="M15")` fetches candles for any detected gaps via `fetch_and_store()`. One-pass only — no retry loop. Unresolvable gaps are logged as `gap_unresolved`, not raised.
result: cancelled
reason: "Broker migration - legacy OANDA"

### 9. APScheduler 4 Jobs Running
expected: After server start, `create_scheduler()` registers exactly 4 jobs: M15 (every 15 min), H1 (every 1h), H4 (every 4h), D1 (00:05 UTC daily). All have `max_instances=1`. Scheduler starts without error; jobs execute on schedule.
result: [pending]

### 10. Health Endpoint last_candle_fetch
expected: `GET /health` returns JSON with a `last_candle_fetch` field. Before any scheduled job has run, each timeframe shows `null`. After at least one job runs, the matching timeframe shows an ISO timestamp. The endpoint returns 200 in both states.
result: [pending]

## Summary

total: 10
passed: 1
issues: 0
pending: 2
skipped: 1
blocked: 0
cancelled: 6

## Gaps

[none yet]
