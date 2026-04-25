---
phase: 05-backtesting-validation
plan: 04
subsystem: historical-data
tags: [histdata, xauusd, offline-loader, provider-gate]

requires:
  - phase: 05-03
    provides: Scheduled WalkForwardOptimizer with insufficient-data guard

provides:
  - IG historical provider gate decision
  - HistData offline bootstrap loader for Phase 5
  - Read-only QA command for downloaded HistData archives

affects: [05-05]

tech-stack:
  added: []
  patterns: [offline bootstrap provider, fixed-offset timezone conversion, idempotent bulk insert]

key-files:
  created:
    - src/backtesting/historical_loader.py
    - scripts/histdata_phase5_loader.py
    - tests/test_backtesting/test_historical_loader.py
  modified:
    - .gitignore

key-decisions:
  - "IG = ig-insufficient: account no longer available and historical /prices probes failed even for max=1"
  - "OANDA remains proscribed: legacy path was tested/rejected and is not active in current repo"
  - "HistData is Phase 5 historical bootstrap only, not a runtime market-data provider"
  - "Runtime XAUUSD provider is deferred; Dukascopy is the likely candidate to validate before Phase 7"
  - "HistData timestamps are treated as fixed EST without DST and converted to UTC at ingestion boundary"

requirements-completed: []

duration: 90min
completed: 2026-04-23
---

# Phase 05-04: Historical Provider Gate + HistData Offline Loader

**IG is insufficient for Phase 5 historical validation; HistData XAUUSD M1 is now the offline bootstrap path for Phase 5 only**

## Provider Decision

- Gate result: `ig-insufficient`
- Rationale: IG account is no longer available and prior historical probes returned `error.public-api.exceeded-account-historical-data-allowance` even for 1-bar `/prices` requests.
- Runtime impact: IG is removed from the active path. There is currently no validated real XAUUSD runtime provider.
- Phase 5 impact: Runtime provider is not required for offline walk-forward validation, so Phase 5 can proceed using HistData historical data.

## HistData Dataset

Downloaded locally under `data/histdata/xauusd_m1_ascii/`:

- `HISTDATA_COM_ASCII_XAUUSD_M1_2024.zip`
- `HISTDATA_COM_ASCII_XAUUSD_M1_2025.zip`
- `HISTDATA_COM_ASCII_XAUUSD_M1_202601.zip`
- `HISTDATA_COM_ASCII_XAUUSD_M1_202602.zip`
- `HISTDATA_COM_ASCII_XAUUSD_M1_202603.zip`
- `HISTDATA_COM_ASCII_XAUUSD_M1_202604.zip`

Source format: Generic ASCII M1 bars:

```text
YYYYMMDD HHMMSS;open_bid;high_bid;low_bid;close_bid;volume
```

HistData documents the timestamp timezone as EST without daylight-saving adjustments. The loader therefore converts fixed UTC-05:00 timestamps to UTC before resampling.

The dataset files are ignored by git via `.gitignore` because they are local vendor data.

## Loader Implementation

- `src/backtesting/historical_loader.py`
  - Parses HistData Generic ASCII M1 rows.
  - Converts fixed EST timestamps to UTC.
  - Resamples M1 to `M15`, `H1`, `H4`, and `D1`.
  - Builds `Candle` rows for existing `candles` table only.
  - Uses dialect-aware `ON CONFLICT DO NOTHING` for idempotent inserts.

- `scripts/histdata_phase5_loader.py`
  - `qa`: read-only archive inspection and resampled row counts.
  - `import`: inserts resampled candles into PostgreSQL.

This does not modify `MarketDataClient`, IG, Binance, or any runtime ingestion path.

## QA Evidence

Command:

```bash
./.venv/bin/python scripts/histdata_phase5_loader.py qa
```

Result summary:

| Timeframe | Resampled rows | Phase 5 minimum | Status |
|---|---:|---:|---|
| M15 | 53,731 | 24,685 | PASS |
| H1 | 13,442 | 6,171 | PASS |
| H4 | 3,637 | 1,542 | PASS |
| D1 | 710 | 257 | PASS |

Archive row counts:

| Archive | M1 rows | First UTC | Last UTC |
|---|---:|---|---|
| 2024 | 355,652 | 2024-01-01T23:00:00Z | 2024-12-31T21:57:00Z |
| 2025 | 354,011 | 2025-01-01T23:00:00Z | 2025-12-31T21:57:00Z |
| 2026-01..04 | 96,147 | 2026-01-01T23:00:00Z | 2026-04-10T21:58:00Z |

HistData status reports include expected market-session gaps and some short intra-session gaps. This should be treated as a data-quality caveat for Phase 5 reporting, not as a blocker for meeting minimum walk-forward candle counts.

## Verification

Automated:

```bash
./.venv/bin/python -m pytest tests/test_backtesting/test_historical_loader.py -q
./.venv/bin/python -m pytest tests/test_backtesting -q
```

Results:

- `6 passed` for historical loader tests.
- `48 passed` for the full backtesting test suite.

## Next Phase Readiness

Plan 05-05 can proceed against the existing `candles` table after running:

```bash
./.venv/bin/python scripts/histdata_phase5_loader.py import
```

Runtime provider remains deferred. Before Phase 7 signal mode, validate Dukascopy or another real XAUUSD runtime feed with a read-only probe.

---
*Phase: 05-backtesting-validation*
*Completed: 2026-04-23*
