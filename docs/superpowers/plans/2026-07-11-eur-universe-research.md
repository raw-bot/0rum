# EUR Universe Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an auditable Kraken EUR candle source, compare the approved five-pair universe with the existing daily Donchian edge, and prevent legacy strategies from reopening after their current paper positions exit.

**Architecture:** Keep research data and portfolio execution separate. A pure CCXT adapter returns only finalized, chronological candles; an offline script performs next-open Donchian simulation and chronological reporting. The active portfolio gains only an `entry_enabled` gate in this pass; no EUR strategy is added until the report is reviewed.

**Tech Stack:** Python 3.11, CCXT 4.5.56, pytest 9, PyYAML, Kraken public Spot REST/CCXT unified `fetch_ohlcv`.

---

### Task 1: Finalized Kraken candle adapter

**Files:**
- Create: `orum/portfolio/ccxt_provider.py`
- Create: `tests/test_ccxt_provider.py`

- [ ] Write a failing test around this interface:

```python
provider = CcxtClosedCandleProvider({"kraken": fake_exchange}, now_ms=lambda: 10_000)
bars = provider("kraken", "BTC/EUR", "4h", 300)
assert bars[-1]["ts"] == 4_000
```

The fixture must prove that inactive/non-spot markets are rejected, timestamps are strictly increasing, malformed OHLC is rejected, and the incomplete tail is removed.

- [ ] Run `uv run python -m pytest tests/test_ccxt_provider.py -q` and verify RED because the module does not exist.
- [ ] Implement `CcxtClosedCandleProvider.__call__(venue, symbol, timeframe, limit)` using `load_markets`, `has['fetchOHLCV']`, `parse_timeframe`, and `fetch_ohlcv(symbol, timeframe, limit=limit + 1)`.
- [ ] Normalize each bar to `ts/open/high/low/close/volume`; require `low <= open,close <= high`; return at most `limit` finalized bars.
- [ ] Run the focused test and verify GREEN.

### Task 2: Legacy entry-only migration gate

**Files:**
- Modify: `orum/portfolio/paper_engine.py`
- Modify: `tests/test_paper_engine.py`
- Modify: `state/portfolio.yaml`

- [ ] Add a failing test configuring:

```python
{"id": "legacy", "engine": "donchian", "entry_enabled": False, ...}
```

and assert a LONG signal cannot open while a later EXIT still closes a persisted position.
- [ ] Run the focused test and verify RED because `StrategyConfig` has no gate.
- [ ] Add `entry_enabled: bool = True` to `StrategyConfig`; parse only real booleans and reject ambiguous strings.
- [ ] In `run_cycle`, map a blocked LONG to intent `entry_disabled`; never block EXIT.
- [ ] Set `entry_enabled: false` for `btc_ak_macd_4h`, `eth_donchian`, and `gold_cot` in `state/portfolio.yaml`. This changes no open position and opens no new one.
- [ ] Run paper-engine and broker tests and verify GREEN.

### Task 3: Five-pair Donchian research harness

**Files:**
- Create: `scripts/research_eur_universe.py`
- Create: `tests/test_research_eur_universe.py`

- [ ] Write failing tests for `simulate_donchian(candles, entry_n=20, exit_n=10, fee_rt=0.001, slippage=0.0003)` proving signal at close `t` fills at open `t+1`, one position maximum, and no forced end-of-data winner.
- [ ] Write a failing test for `chronological_report(trades, split_fraction=0.70)` returning full/train/holdout trade count, PF, expectancy R, maximum realized drawdown R, and exposure.
- [ ] Implement the minimal simulator using risk distance `2 * ATR14(signal bar)` and no leverage; report results in R so EUR and USDT remain comparable without mixing account currencies.
- [ ] Implement the fixed universe `BTC/EUR`, `ETH/EUR`, `SOL/EUR`, `XRP/EUR`, `AAVE/EUR`; fetch through `CcxtClosedCandleProvider` and write no portfolio state.
- [ ] Mark a pair `provisional_only` whenever Kraken history has fewer than 1,000 finalized daily bars or the holdout has fewer than 20 closed trades; never auto-promote.
- [ ] Run the focused tests and verify GREEN.

### Task 4: Public-data execution and evidence

**Files:**
- Create: `docs/research/eur-universe-2026-07-11.md`

- [ ] Run the five-pair script against Kraken public data with no API key.
- [ ] Record the exact retrieval time, bar count, first/last finalized candle, fees, slippage, train/holdout split, and results.
- [ ] State explicitly that Kraken OHLC is capped at 720 recent entries and cannot by itself establish a multi-cycle production edge.
- [ ] Do not edit `state/portfolio.yaml` beyond the legacy `entry_enabled: false` gates.

### Task 5: Verification

**Files:**
- Verify all files above.

- [ ] Run `uv run python -m pytest tests/test_ccxt_provider.py tests/test_research_eur_universe.py tests/test_paper_engine.py tests/test_paper_broker.py tests/test_strategy_registry.py -q`.
- [ ] Run the full test suite.
- [ ] Run GitNexus change detection and verify only research/provider and the entry gate affect execution.
- [ ] Confirm `state/paper_positions.json`, fills and equity journals were not edited by this task.
- [ ] Do not restart the paper worker, dashboard, watcher, or producer.
