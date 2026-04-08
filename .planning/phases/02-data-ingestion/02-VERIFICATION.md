---
phase: 02-data-ingestion
verified: 2026-04-08T00:00:00Z
status: gaps_found
score: 3/5 must-haves verified
gaps:
  - truth: "13 unit tests pass without live OANDA connection or PostgreSQL (DATA-02)"
    status: failed
    reason: "Test count changed from 13 to 15 (market_client replaced OANDA client) and test collection fails in a fresh environment because .env contains OANDA_ fields that are now forbidden by Settings model (extra=forbid). `pytest tests/test_ingestion/ -x` produces 2 collection errors. Tests only pass if .env lacks OANDA_ keys."
    artifacts:
      - path: "tests/test_ingestion/test_oanda_client.py"
        issue: "Stub — 2-line comment file with zero test functions. OandaClient tests replaced by test_market_client.py but test_oanda_client.py was not deleted, just emptied. Creates a misleading placeholder."
      - path: "tests/conftest.py"
        issue: "Does not remove OANDA_ env vars from the environment before test collection. Settings model has extra=forbid, so the existing .env with OANDA_API_KEY / OANDA_ACCOUNT_ID / OANDA_API_URL causes ValidationError on every import of src.ingestion.candle_fetcher or src.ingestion.gap_detector."
      - path: ".env"
        issue: "Still contains OANDA_API_KEY=your-api-key, OANDA_ACCOUNT_ID=your-account-id, OANDA_API_URL=... which are now forbidden extra fields in Settings."
    missing:
      - "Remove OANDA_ keys from .env (or .env.example) since Settings no longer accepts them"
      - "Add os.environ.pop / os.environ overrides in conftest.py to neutralize any pre-existing OANDA_ env vars so collection succeeds regardless of .env state"
      - "Either delete tests/test_ingestion/test_oanda_client.py or add a module-level comment documenting it is intentionally empty"

  - truth: "On first startup the bot automatically backfills 6 months of candles for all 4 timeframes"
    status: partial
    reason: "Code path exists and is wired correctly (asyncio.create_task(fetcher.backfill_all()) in lifespan). However the data source changed mid-phase from OANDA v20 to Binance PAXG/USDT proxy (commits 7d0d706 and 0e0d3ca). The proxy source is explicitly documented as a temporary stand-in that differs from real XAU/USD in microstructure, 24/7 trading, and USDT denomination. CLAUDE.md spec and ROADMAP both require XAUUSD (XAU/USD) candles — the proxy is not XAUUSD data. Backfill mechanics work but the instrument correctness is unvalidated."
    artifacts:
      - path: "src/ingestion/market_client.py"
        issue: "Fetches PAXG/USDT from Binance and maps it to instrument='XAUUSD' internally. The docstring explicitly warns this is a proxy with crypto microstructure differences. Phase spec requires real XAU/USD data."
    missing:
      - "Either: connect a real XAU/USD data source before Phase 3 builds strategy signals on this data, OR document this as an accepted interim deviation with a Phase 7 remediation plan"
---

# Phase 02: Data Ingestion Verification Report

**Phase Goal:** XAUUSD candles for M15, H1, H4, D1 accumulate continuously in PostgreSQL with no gaps
**Verified:** 2026-04-08
**Status:** gaps_found
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | On first startup the bot automatically backfills 6 months of candles for all 4 timeframes | PARTIAL | `asyncio.create_task(fetcher.backfill_all())` is in lifespan (line 54 main.py). Backfill logic exists and paginates. However data source is Binance PAXG/USDT proxy, not real XAU/USD. |
| 2 | The scheduler refreshes each timeframe on its correct cadence (M15 every 15 min, H1 every hour, H4 every 4h, D1 daily at 00:05 UTC) | VERIFIED | `create_scheduler()` produces exactly 4 jobs: interval[0:15:00], interval[1:00:00], interval[4:00:00], cron[hour='0', minute='5']. All with max_instances=1. Confirmed by live execution. |
| 3 | Any detected candle gap triggers an automatic backfill and is logged as a structured event | VERIFIED | GapDetector.detect_and_fill() logs gap_detector.gap_detected (warning), gap_detector.gap_filled (info), gap_detector.gap_unresolved (warning). Called after every scheduled fetch in _refresh_timeframe(). |
| 4 | After 24 hours of operation the `candles` table contains no gaps in any timeframe | UNCERTAIN | Cannot verify programmatically without a running instance. Mechanism is in place (scheduler + gap detector + backfill). Needs human verification after 24h uptime. See Human Verification section. |
| 5 | 13 unit tests pass without live OANDA connection or PostgreSQL (DATA-02) | FAILED | `pytest tests/test_ingestion/ -x` fails with 2 collection errors. .env contains OANDA_ vars that Settings (extra=forbid) rejects. test_oanda_client.py is an empty stub. The suite has 15 tests across 3 active files, not 13. Only 7 tests (test_market_client.py) pass in a clean run. |

**Score:** 3/5 truths verified (2 VERIFIED, 1 PARTIAL, 1 UNCERTAIN, 1 FAILED)

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/ingestion/__init__.py` | Package marker | VERIFIED | Exists, empty |
| `src/ingestion/oanda_client.py` | Async OANDA v20 candle client | STUB | File is a 3-line re-export alias. OandaClient = MarketDataClient from market_client.py. No OANDA v20 implementation. |
| `src/ingestion/market_client.py` | Binance PAXG/USDT client (replacement) | VERIFIED | 112 lines, MarketDataClient class, get_candles(), D1→1d mapping, ccxt.binance, normalization |
| `src/ingestion/candle_fetcher.py` | Fetch, store, and backfill candles | VERIFIED | CandleFetcher class with fetch_and_store, backfill_timeframe, backfill_all, prune_incomplete. Uses pg_insert ON CONFLICT DO NOTHING. |
| `src/ingestion/gap_detector.py` | Gap detection and backfill trigger | VERIFIED | GapDetector with find_gaps(), detect_and_fill(), TIMEFRAME_MINUTES map, Gap NamedTuple |
| `src/scheduler/__init__.py` | Package marker | VERIFIED | Exists, empty |
| `src/scheduler/jobs.py` | APScheduler job definitions | VERIFIED | create_scheduler() with 4 jobs, correct cadences, max_instances=1 |
| `src/main.py` | FastAPI lifespan wiring | VERIFIED | asyncio.create_task(backfill_all()), create_scheduler()/start()/shutdown() wired |
| `tests/test_ingestion/__init__.py` | Package marker | VERIFIED | Exists |
| `tests/test_ingestion/test_oanda_client.py` | OandaClient tests | STUB | Empty 2-line comment file, 0 test functions |
| `tests/test_ingestion/test_market_client.py` | MarketDataClient tests (replacement) | VERIFIED | 7 tests, all pass, ccxt exchange mocked |
| `tests/test_ingestion/test_candle_fetcher.py` | CandleFetcher unit tests | VERIFIED (conditionally) | 4 tests, pass when .env lacks OANDA_ keys. Fail to collect in default env due to Settings validation error. |
| `tests/test_ingestion/test_gap_detector.py` | GapDetector unit tests | VERIFIED (conditionally) | 4 tests, pass when .env lacks OANDA_ keys. Same collection error as above. |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `src/ingestion/candle_fetcher.py` | candles table | pg_insert ON CONFLICT DO NOTHING | VERIFIED | Line 112: `.on_conflict_do_nothing(index_elements=["instrument", "timeframe", "timestamp"])` |
| `src/scheduler/jobs.py` | `src/ingestion/candle_fetcher.py` | CandleFetcher.fetch_and_store() | VERIFIED | _refresh_timeframe() calls fetcher.fetch_and_store() then detector.detect_and_fill() |
| `src/scheduler/jobs.py` | `src/ingestion/gap_detector.py` | GapDetector.detect_and_fill() | VERIFIED | Called after every fetch in _refresh_timeframe() |
| `src/main.py` | `src/scheduler/jobs.py` | scheduler.start() in lifespan | VERIFIED | Lines 59-60 main.py: create_scheduler() + scheduler.start() before yield |
| `src/monitoring/health.py` | `src/scheduler/jobs.py` | get_last_candle_fetch() | VERIFIED | Line 13: import get_last_candle_fetch; line 72: "last_candle_fetch": get_last_candle_fetch() |
| `src/ingestion/oanda_client.py` | OANDA v20 API | httpx.AsyncClient | NOT_WIRED | oanda_client.py is a 3-line stub that re-exports MarketDataClient. No OANDA HTTP calls exist. |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `src/ingestion/candle_fetcher.py` | raw_candles | MarketDataClient.get_candles() | Yes (Binance public API, PAXG/USDT) | FLOWING — but instrument is a proxy, not XAUUSD |
| `src/ingestion/gap_detector.py` | timestamps | AsyncSessionLocal candles SELECT | Yes (queries real DB rows) | FLOWING |
| `src/scheduler/jobs.py` | _last_candle_fetch | datetime.now(utc).isoformat() after fetch | Yes | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Scheduler creates 4 jobs with correct triggers | `.venv/bin/python /tmp/test_sched.py` | 4 jobs: interval[0:15:00], interval[1:00:00], interval[4:00:00], cron[hour='0', minute='5'] | PASS |
| test_market_client.py (7 tests) | `.venv/bin/pytest tests/test_ingestion/test_market_client.py -v` | 7 passed | PASS |
| test_candle_fetcher.py (4 tests) | `pytest tests/test_ingestion/test_candle_fetcher.py` in clean env | 4 passed | PASS (clean env only) |
| test_gap_detector.py (4 tests) | `pytest tests/test_ingestion/test_gap_detector.py` in clean env | 4 passed | PASS (clean env only) |
| Full suite `pytest tests/test_ingestion/ -x` | Standard invocation with repo .env | COLLECTION ERROR (2 files) | FAIL |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| DATA-01 | 02-01, 02-02, 02-03 | OANDA candle ingestion for M15, H1, H4, D1 timeframes with auto-backfill on startup | PARTIAL | Ingestion pipeline exists and auto-backfill is wired. Data source is Binance proxy, not OANDA. Scheduler cadences are correct. |
| DATA-02 | 02-02, 02-03 | Gap detection flags missing candle windows before strategy execution | PARTIAL | GapDetector is implemented and wired. Unit tests pass in clean env. Test suite fails to collect in default env due to .env/.Settings mismatch. |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `tests/test_ingestion/test_oanda_client.py` | 1-3 | Empty placeholder file with 0 tests, comment says replaced | Warning | Misleading — plan counted 5 OandaClient tests but file is empty. Contributes to the "13 tests" count being wrong. |
| `.env` | 1-3 | OANDA_API_KEY, OANDA_ACCOUNT_ID, OANDA_API_URL still present | Blocker | Settings model has extra=forbid. These keys cause ValidationError on every module import that triggers Settings(). Blocks test collection for test_candle_fetcher.py and test_gap_detector.py. |
| `tests/conftest.py` | 8-14 | Missing suppression of OANDA_ env vars inherited from .env | Blocker | conftest.py sets required fields with setdefault but does not remove the now-forbidden OANDA_ fields that .env injects. |
| `src/ingestion/market_client.py` | 1-11 | Docstring explicitly marks PAXG/USDT as a proxy with noted differences vs real XAU/USD | Warning | "Before Phase 7 (execution): validate strategy signals against a real XAU/USD source" — risks strategy layer (Phase 3+) being built on non-representative data. |

---

### Human Verification Required

#### 1. 24-Hour Gap-Free Operation

**Test:** Let the bot run against a PostgreSQL instance for 24 hours. Query `SELECT timeframe, COUNT(*), MIN(timestamp), MAX(timestamp) FROM candles WHERE instrument='XAUUSD' GROUP BY timeframe` at T+24h.
**Expected:** No temporal gaps exceeding 2× the expected interval (30 min for M15, 2h for H1, 8h for H4, 2 days for D1) in any timeframe outside weekend windows.
**Why human:** Requires a running instance with a live database and 24h elapsed time. Cannot be verified statically.

---

### Gaps Summary

**Gap 1 — Test suite fails to collect in default environment (Blocker)**

The migration from OANDA to Binance updated `src/ingestion/oanda_client.py` (now a stub), `src/ingestion/candle_fetcher.py` (now uses MarketDataClient), and `src/config.py` (OANDA fields removed). However `.env` was not updated to remove `OANDA_API_KEY`, `OANDA_ACCOUNT_ID`, and `OANDA_API_URL`. The Settings model has `extra=forbid` inherited from pydantic-settings defaults, so any import chain that reaches `src/database.py` (which calls `get_settings()` at module level) fails with ValidationError. This prevents `pytest tests/test_ingestion/` from collecting `test_candle_fetcher.py` and `test_gap_detector.py`. The stated requirement — "13 unit tests pass without live OANDA connection or PostgreSQL" — cannot be met in the current state.

**Gap 2 — Data source is a proxy, not XAUUSD (Warning)**

The phase goal is "XAUUSD candles accumulate continuously in PostgreSQL." The actual data stored comes from Binance PAXG/USDT, which trades 24/7 with crypto-market microstructure. The `market_client.py` docstring explicitly documents this as a temporary proxy requiring replacement before Phase 7. The implication: Phase 3 strategy logic and Phase 5 backtesting will run on PAXG/USDT price history labeled as XAUUSD. This is an acknowledged in-progress substitution, not an implementation bug, but it means the phase goal as stated in the ROADMAP is not fully achieved.

---

## Deferred Items

None — all gaps identified are actionable in the current phase.

---

_Verified: 2026-04-08_
_Verifier: Claude (gsd-verifier)_
