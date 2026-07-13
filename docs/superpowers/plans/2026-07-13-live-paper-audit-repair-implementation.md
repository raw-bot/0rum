# Live Paper Audit Repair Implementation Plan

**Goal:** Make the unified paper portfolio, its exits and its forecast history
auditable without placing real orders or restarting live processes.

**Architecture:** Extend the existing position schema compatibly, monitor
protective levels independently of signal deduplication, preserve forecast
state per strategy, append forecast predictions/realizations, and make the
dashboard distinguish unified fills, model markers and legacy history.

**Risk and rollback:** Paper fills and account state are financial audit data.
Tests use an isolated `0RUM_STATE_DIR`; implementation never truncates journals.
Rollback reverts code and optional metadata only. No live process control is
part of this plan.

---

### Task 1: Prove the position lifecycle failures

**Files:**
- Modify: `tests/test_paper_broker.py`
- Modify: `tests/test_paper_engine.py`

- [ ] Add failing tests for backward-compatible optional exit fields.
- [ ] Add failing tests for AK structural brackets and the existing-position
  ATR fallback migration.
- [ ] Add failing tests for SL, TP, same-candle SL/TP collision and bracket
  monitoring when the H4 signal candle is a duplicate.
- [ ] Add a failing test proving the UT protective stop does not invent a TP.

### Task 2: Implement persisted and enforceable exit policies

**Files:**
- Modify: `orum/portfolio/paper_broker.py`
- Modify: `orum/portfolio/paper_engine.py`

- [ ] Add optional position/fill metadata with safe deserialization defaults.
- [ ] Derive new-entry policies from the strategy signal and configuration.
- [ ] Migrate only missing AK metadata from persisted `atr_risk`.
- [ ] Evaluate protective exits from closed monitoring OHLC before strategy
  signal processing; fill the frozen level and record the exact reason.
- [ ] Keep signal exits and aggregate risk behavior unchanged.

### Task 3: Prove and implement six-hour forecast history

**Files:**
- Modify: `orum/paths.py`
- Modify: `orum/portfolio/paper_engine.py`
- Modify: `tests/test_paper_engine.py`

- [ ] Add failing tests showing duplicate strategy candles do not erase latest
  forecast state.
- [ ] Add failing tests for one prediction per strategy/UTC 6-hour bucket and
  append-only +6/+12/+24 realizations.
- [ ] Merge latest state from disk before updating healthy strategies.
- [ ] Append compact prediction and realization records without reconstructing
  earlier history.

### Task 4: Prove dashboard truth labels and payloads

**Files:**
- Modify: `tests/test_dashboard_state.py`
- Modify: `tests/test_dashboard_terminal.py`

- [ ] Add failing tests for `IN L`/`IN S`, reason-based `TP`/`SL`/`OUT`, model
  prefixes and position exit-policy fields.
- [ ] Add failing tests for strict strategy forecast lookup, forecast history
  payloads and separated legacy weekly history.
- [ ] Add static assertions for 50% yellow prediction opacity and the
  predicted-versus-real legend.

### Task 5: Implement API and dashboard corrections

**Files:**
- Modify: `orum/dashboard.py`
- Modify: `orum/static/dashboard.html`
- Modify: `orum/static/dashboard.js`
- Modify: `orum/static/dashboard.css`

- [ ] Expose enriched unified positions and a separately labelled legacy audit.
- [ ] Resolve forecasts only by strategy and compose prediction/realization
  history for each market card.
- [ ] Draw real/model markers and SL/TP lines from enforced metadata only.
- [ ] Draw archived yellow p50 at 50% plus the realized path and error labels.
- [ ] Show a visible legacy-process conflict warning until operational cutover.

### Task 6: Verify impact and behavior

**Files:**
- Verify modified files only.

- [ ] Run GitNexus impact for every edited symbol and API route before edits.
- [ ] Run focused broker, paper-engine and dashboard tests through RED then
  GREEN.
- [ ] Run the full test suite and the paper runner's dry construction.
- [ ] Inspect `/api/state` output and render the dashboard against isolated test
  state without restarting the live dashboard.
- [ ] Run GitNexus `detect_changes` and review affected execution flows.
- [ ] Confirm no process was stopped/restarted and request explicit approval for
  the operational legacy cutover.
