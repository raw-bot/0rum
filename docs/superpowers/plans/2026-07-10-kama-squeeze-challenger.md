# KAMA Squeeze Challenger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, inactive KAMA squeeze challenger and compare it fairly with the validated BTC/USDT 4h AK-MACD strategy without opening another paper position.

**Architecture:** Put all indicator and trade-state calculations in one pure strategy module implementing the existing `StrategyEngine` contract. Register the engine by name, but do not add it to `state/portfolio.yaml`; a separate offline comparison script performs next-bar-open simulation at 1x with a 0.5% risk budget and 10% notional cap. Existing paper state and AK-MACD positions remain untouched.

**Tech Stack:** Python 3.11, NumPy, unittest/pytest, existing 0rum strategy registry and Binance research loader.

---

### Task 1: Freeze the executable strategy contract

**Files:**
- Create: `docs/decisions/ADR-001-kama-squeeze-challenger.md`

- [ ] **Step 1:** Record the exact KAMA, squeeze, momentum, ATR, confirmed-bar, stop and next-bar execution semantics.
- [ ] **Step 2:** Record the two explicit assumptions missing from the supplied settings: ATR length 14 and linear-regression momentum length 16.
- [ ] **Step 3:** Record that 10x and ATR regime multipliers are rejected; challenger research uses 1x, 0.5% account risk and a 10% notional cap.
- [ ] **Step 4:** Verify the ADR says the challenger is registry-loadable but absent from the active portfolio configuration.

### Task 2: Implement indicators and entry logic with TDD

**Files:**
- Create: `tests/test_kama_squeeze.py`
- Create: `orum/strategies/kama_squeeze.py`

- [ ] **Step 1:** Write failing tests for KAMA efficiency ratio, zero-volatility handling, squeeze duration/release, positive rising regression momentum and confirmed long entry.
- [ ] **Step 2:** Run `uv run pytest tests/test_kama_squeeze.py -q` and verify failures are caused by the missing module.
- [ ] **Step 3:** Implement `KamaSqueezeParams`, Wilder ATR, KAMA, BB/KC squeeze state and endpoint linear-regression momentum using closed candles only.
- [ ] **Step 4:** Implement the exact entry predicate: release after at least two squeeze bars, positive/rising momentum, close above a one-bar-rising KAMA and ER strictly above 0.20.
- [ ] **Step 5:** Run `uv run pytest tests/test_kama_squeeze.py -q` and verify the entry/indicator tests pass.

### Task 3: Implement deterministic exits with TDD

**Files:**
- Modify: `tests/test_kama_squeeze.py`
- Modify: `orum/strategies/kama_squeeze.py`

- [ ] **Step 1:** Write failing tests for initial stop `fill - 2.8*ATR(signal)`, non-decreasing highest-close `5*ATR` trail, close-based next-bar exit intent, and force exit below KAMA with negative momentum.
- [ ] **Step 2:** Run the focused tests and verify the expected failures.
- [ ] **Step 3:** Implement a pure candle replay that reconstructs flat/long state from history, so engine restarts do not lose trailing-stop state.
- [ ] **Step 4:** Ensure the initial stop stays active until the monotonic trail becomes tighter and same-bar high/low never produces an exit.
- [ ] **Step 5:** Run the complete KAMA test module and verify it passes.

### Task 4: Register the inactive challenger

**Files:**
- Modify: `orum/strategies/__init__.py`
- Modify: `tests/test_strategy_registry.py`

- [ ] **Step 1:** Write a failing registry test loading `kama_squeeze` with its supplied parameters.
- [ ] **Step 2:** Run the registry test and verify it fails as an unknown built-in.
- [ ] **Step 3:** Add `KamaSqueezeEngine` to `_ENGINES` without changing registry resolution behavior.
- [ ] **Step 4:** Run `uv run pytest tests/test_strategy_registry.py tests/test_kama_squeeze.py -q`.
- [ ] **Step 5:** Verify `state/portfolio.yaml` has no `kama_squeeze` entry.

### Task 5: Add the fair offline comparison

**Files:**
- Create: `tests/test_kama_challenger_backtest.py`
- Create: `scripts/backtest_kama_challenger.py`

- [ ] **Step 1:** Write failing tests proving signal-close information fills only at the next bar open, position notional never exceeds 10% of equity, and planned stop loss never exceeds 0.5% before execution gaps.
- [ ] **Step 2:** Run the focused tests and verify expected failures.
- [ ] **Step 3:** Implement deterministic KAMA trade simulation with 0.1% round-trip fees, explicit slippage, chronological split and yearly metrics.
- [ ] **Step 4:** Reuse the existing AK-MACD confirmed-entry implementation and print comparable trade count, PF, expectancy, drawdown and exposure; never change parameters from output.
- [ ] **Step 5:** Run unit tests offline, then run the comparison only against an already cached dataset or with separately approved network access.

### Task 6: Verify scope and safety

**Files:**
- Verify: all files above

- [ ] **Step 1:** Run `uv run pytest tests/test_kama_squeeze.py tests/test_kama_challenger_backtest.py tests/test_strategy_registry.py tests/test_paper_engine.py tests/test_paper_broker.py -q`.
- [ ] **Step 2:** Run the broader strategy test set and confirm no regression.
- [ ] **Step 3:** Run GitNexus change detection and inspect all affected symbols and flows.
- [ ] **Step 4:** Confirm `state/paper_positions.json`, journals and live configuration are byte-for-byte unmodified.
- [ ] **Step 5:** Do not restart worker, watcher, dashboard or producer; report that runtime activation still requires explicit approval.
