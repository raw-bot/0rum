# BTC UT Bot M15/H1 Direct Paper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a direct-paper BTC M15/H1 UT Bot sleeve beside the existing BTC H4 sleeve, sharing one risk-capped portfolio and using a separate dashboard card.

**Architecture:** Extend the strategy context with optional multi-timeframe candles, implement UT Bot as a pure strategy engine, enforce aggregate risk before broker entry, and key the new dashboard surface by strategy card rather than symbol alone. Preserve all existing single-timeframe callers through defaults.

**Tech Stack:** Python 3, dataclasses, unittest, YAML, vanilla JavaScript dashboard, GitNexus impact analysis.

---

### Task 1: Multi-timeframe strategy contract

**Files:**
- Modify: `orum/strategies/base.py`
- Modify: `orum/portfolio/paper_engine.py`
- Test: `tests/test_paper_engine.py`

- [ ] Add a failing test whose provider records requests and proves a strategy declaring `required_timeframes = ["15m", "1h"]` receives both lists while its primary `context.candles` stays M15.
- [ ] Run `uv run python -m unittest tests.test_paper_engine -v` and confirm failure because `candles_by_timeframe` is absent.
- [ ] Add `candles_by_timeframe: dict[str, list[dict]] = field(default_factory=dict)` to `StrategyContext` and fetch declared timeframes once per strategy in `PaperEngine.run_cycle`.
- [ ] Re-run the focused test and confirm it passes with existing tests unchanged.

### Task 2: Pure UT Bot M15/H1 engine

**Files:**
- Create: `orum/strategies/utbot_mtf.py`
- Modify: `orum/strategies/__init__.py`
- Create: `tests/test_utbot_mtf.py`
- Modify: `tests/test_strategy_registry.py`

- [ ] Write failing tests for Wilder ATR/trailing-stop crossover, H1 EMA200 entry permission, duplicate M15 timestamp suppression, and SELL returning `Side.EXIT`.
- [ ] Run `uv run python -m unittest tests.test_utbot_mtf tests.test_strategy_registry -v` and confirm the engine is missing.
- [ ] Implement `UtBotMtfEngine` with fixed defaults (`6/10`, `7/20`, H1 EMA200), closed-candle inputs, and no I/O.
- [ ] Register `utbot_mtf` and make all focused tests pass.

### Task 3: Aggregate paper risk caps

**Files:**
- Modify: `orum/portfolio/paper_engine.py`
- Test: `tests/test_paper_engine.py`

- [ ] Add failing tests proving two same-symbol entries can coexist when under cap and that the second is blocked when total or BTC stop-risk would exceed its cap.
- [ ] Confirm RED with the focused unittest command.
- [ ] Compute open stop risk from `risk_pct * equity_for_sizing`, include fills already accepted in the current cycle, and block only the new entry with an inspectable intent.
- [ ] Confirm GREEN and preserve existing positions unchanged.

### Task 4: Activate the second paper sleeve and 15-minute cadence

**Files:**
- Modify: `state/portfolio.yaml`
- Modify: `scripts/run_paper_portfolio.py`
- Test: `tests/test_paper_engine.py`

- [ ] Add a config-contract test for `btc_utbot_m15_h1`, risk `0.005`, primary `15m`, and active entries.
- [ ] Add the sleeve plus total/per-symbol risk caps to the YAML.
- [ ] Change loop sleep to an explicit configurable 900-second default and retain `--once` behavior.
- [ ] Run dry configuration construction without fetching market data.

### Task 5: Separate BTC M15/H1 dashboard card

**Files:**
- Modify: `orum/dashboard.py`
- Modify: `orum/static/dashboard.html`
- Modify: `orum/static/dashboard.js`
- Test: `tests/test_dashboard_terminal.py`
- Test: `tests/test_dashboard_state.py`

- [ ] Add failing backend tests for two BTC card keys and `strategy_id`-filtered real markers.
- [ ] Add failing static-contract assertions for the new card ID and card-key mapping.
- [ ] Extend `_market_signals` without altering the existing H4 payload, add the M15/H1 payload, and filter fills by strategy ID.
- [ ] Add a second BTC card and make renderer state (visibility, zoom, pan, position lookup) card-key aware.
- [ ] Run the focused dashboard tests and confirm GREEN.

### Task 6: Verification and change audit

**Files:**
- Verify all modified files only.

- [ ] Run focused portfolio, strategy and dashboard tests.
- [ ] Run `uv run python -m unittest discover -s tests`.
- [ ] Run `scripts/run_paper_portfolio.py --dry` and verify both BTC sleeves build.
- [ ] Run GitNexus `detect_changes` and inspect every affected flow.
- [ ] Confirm no process was restarted and report that activation requires explicit restart approval.
