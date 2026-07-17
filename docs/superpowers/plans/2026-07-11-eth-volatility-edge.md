# ETH Volatility Edge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Test whether ETH/EUR's excess volatility supports a portable, out-of-sample spot edge, then safely restart the existing paper runtime so approved configuration changes are loaded.

**Architecture:** Keep historical acquisition, pure strategy simulation, reporting, and runtime operation separate. Retrieve long EUR candles from Coinbase in bounded pages, cross-check the overlapping returns against Kraken, evaluate a frozen ETH challenger through expanding walk-forward windows, and never auto-promote research into the portfolio.

**Tech Stack:** Python 3.11, CCXT 4.5.56, pytest 9, Coinbase Exchange public REST, Kraken public REST, macOS launchd.

---

### Task 1: Coinbase daily history provider

**Files:**
- Create: `orum/research/__init__.py`
- Create: `orum/research/coinbase_history.py`
- Create: `tests/test_coinbase_history.py`

- [ ] Write tests using a fake exchange that prove 300-bar pagination, chronological normalization, boundary filtering, deduplication, incomplete-tail removal, and rejection of inactive/non-spot markets and malformed candles.
- [ ] Run `uv run python -m pytest tests/test_coinbase_history.py -q` and verify RED because the module does not exist.
- [ ] Implement a read-only `fetch_daily_history(exchange, symbol, start_ms, end_ms, now_ms)` function using CCXT `load_markets` and `fetch_ohlcv` with explicit `since` cursors and `limit=300`.
- [ ] Advance the cursor from the latest accepted timestamp, reject a page that makes no progress, and return only unique finalized bars in `[start_ms, end_ms)`.
- [ ] Run the focused test and verify GREEN.

### Task 2: Frozen ETH challenger

**Files:**
- Create: `scripts/research_eth_edge.py`
- Create: `tests/test_research_eth_edge.py`

- [ ] Write synthetic-candle tests proving each of the four entry gates, next-open execution, Donchian exit, one-position behavior, cost application, and no forced terminal exit.
- [ ] Write tests proving timestamp intersection for ETH/BTC, return-portability statistics, and non-overlapping walk-forward assignment by entry signal.
- [ ] Run `uv run python -m pytest tests/test_research_eth_edge.py -q` and verify RED because the module does not exist.
- [ ] Implement the exact frozen rules from the design using pure functions and reuse the existing Wilder ATR and metric conventions without changing the benchmark.
- [ ] Implement the promotion-gate report with the exact thresholds from the design; output `eligible: false` whenever any threshold or venue-portability gate fails.
- [ ] Run the focused tests and verify GREEN.

### Task 3: Public-data study

**Files:**
- Create: `docs/research/eth-volatility-edge-2026-07-11.md`
- Create at runtime only: `state/data_cache/coinbase_btc_eur_1d.json`
- Create at runtime only: `state/data_cache/coinbase_eth_eur_1d.json`

- [ ] Retrieve public BTC/EUR and ETH/EUR daily candles from the first common available date through the last finalized UTC day.
- [ ] Retrieve the latest 720 finalized Kraken daily candles and calculate venue-portability statistics on common timestamps.
- [ ] Run the frozen benchmark and challenger walk-forward without parameter search.
- [ ] Record timestamps, counts, costs, every gate result, per-window metrics, aggregate metrics, and a clear activate/do-not-activate decision.

### Task 4: Verification and controlled paper restart

**Files:**
- Verify: `state/portfolio.yaml`
- Verify: `state/paper_positions.json`
- Verify: `state/paper_fills.jsonl`
- Verify: `state/paper.err`

- [ ] Run focused tests and the complete suite.
- [ ] Snapshot paper positions, fills, relevant PIDs, and state hashes.
- [ ] Run GitNexus change detection and confirm no runtime strategy or execution symbol was changed by the ETH research.
- [ ] Restart only `com.0rum.engine` with `launchctl kickstart -k gui/$(id -u)/com.0rum.engine`.
- [ ] Trigger `com.0rum.paper` once with `launchctl kickstart -k gui/$(id -u)/com.0rum.paper` so configuration is reloaded.
- [ ] Verify a single launchd-supervised engine chain, successful paper execution, unchanged pre-existing positions, no unexpected fill, dashboard continuity, and clean logs.

