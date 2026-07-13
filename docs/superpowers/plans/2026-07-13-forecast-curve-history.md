# Forecast Curve History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a passive seven-day history of what each frozen p50 forecast predicted versus the candle closes that actually followed it.

**Architecture:** Keep `state/forecast_history.jsonl` append-only and unchanged. Expand the dashboard API window to 28 six-hour predictions, then make the browser derive two elapsed 24-hour paths for every record: interpolated p50 in yellow and observed closes in white. Matured realization records remain the source of `Δ50` audit labels.

**Tech Stack:** Python 3.11 `unittest`, vanilla JavaScript/SVG, Node.js syntax/runtime verification, existing dashboard HTTP server.

---

### Task 1: Seven-day API history window

**Files:**
- Modify: `tests/test_dashboard_terminal.py`
- Modify: `orum/dashboard.py:1011-1100`

- [ ] **Step 1: Write the failing API retention test**

Create 30 valid `prediction` JSONL rows for `btc_utbot_m15_h1`, with distinct six-hour `bucket_ts` and `origin_ts` values, call `_market_signals()` against patched candles, and assert:

```python
history = signals["BTC/USDT::btc_utbot_m15_h1"]["forecast_history"]
self.assertEqual(len(history), 28)
self.assertEqual(history[0]["origin_price"], 60_002)
self.assertEqual(history[-1]["origin_price"], 60_029)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run python -m unittest \
  tests.test_dashboard_terminal.DashboardTerminalContractTests.test_market_forecast_history_keeps_seven_days
```

Expected: FAIL because `_market_signals()` currently returns only 12 records.

- [ ] **Step 3: Implement the 28-record contract**

Add a module constant and use it in both normal and error payloads:

```python
FORECAST_HISTORY_VISIBLE_RECORDS = 28

"forecast_history": compose_forecast_history(
    forecast_history_records, strategy_id=strategy_id
)[-FORECAST_HISTORY_VISIBLE_RECORDS:],
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2. Expected: one passing test.

### Task 2: Progressive historical predicted-versus-actual paths

**Files:**
- Modify: `tests/test_dashboard_terminal.py`
- Modify: `orum/static/dashboard.js:834-870`
- Modify: `orum/static/dashboard.html`

- [ ] **Step 1: Add a Node-backed regression harness for the real JavaScript**

Extract the pure forecast-history functions from `dashboard.js`, evaluate them with Node, and pass a two-hour fixture:

```javascript
const history = [{
  origin_ts: "2026-07-13T06:00:00Z",
  origin_price: 100,
  horizons: {6:{p50:.06}, 12:{p50:.12}, 24:{p50:.24}},
  realized: {}
}];
const candles = [
  {ts: Date.parse("2026-07-13T06:00:00Z"), close: 100},
  {ts: Date.parse("2026-07-13T07:00:00Z"), close: 98},
  {ts: Date.parse("2026-07-13T08:00:00Z"), close: 103}
];
```

Assert that the first segment has predicted prices `[100, 101, 102]` and realized prices `[100, 98, 103]`. Also assert a 30-hour fixture stops both paths at +24 h and that the builder does not slice the supplied history to four records.

- [ ] **Step 2: Run the JavaScript regression and verify RED**

Run:

```bash
uv run python -m unittest \
  tests.test_dashboard_terminal.DashboardTerminalContractTests.test_forecast_history_draws_progressively_before_six_hours
```

Expected: FAIL because the existing builder emits only the origin point before +6 h.

- [ ] **Step 3: Implement elapsed p50 interpolation and observed closes**

Add a pure interpolator and replace the endpoint-only builder:

```javascript
function forecastMedianReturnAtHour(record, elapsedHours) {
  const anchors = [
    {hour: 0, value: 0},
    ...[6, 12, 24].map(hour => ({
      hour,
      value: Number(((record.horizons || {})[String(hour)] || {}).p50),
    })),
  ];
  if (anchors.some(anchor => !Number.isFinite(anchor.value))) return null;
  const hour = Math.max(0, Math.min(24, elapsedHours));
  const right = anchors.findIndex(anchor => anchor.hour >= hour);
  if (right <= 0) return anchors[0].value;
  const left = anchors[right - 1];
  const weight = (hour - left.hour) / (anchors[right].hour - left.hour);
  return left.value + (anchors[right].value - left.value) * weight;
}
```

For every candle strictly after the prediction origin and no later than +24 h/current time, append one yellow predicted point derived from the frozen p50 anchors and one white realized point using `candle.close`. Keep matured realization rows in `audited` so `renderForecastAudit()` can place the latest `Δ50` label without relying on a continuous point's `error` field.

- [ ] **Step 4: Remove the four-record frontend truncation**

Iterate over the complete API window:

```javascript
return (history || []).map(record => {
```

Do not add a picker or replay control.

- [ ] **Step 5: Bump the browser asset version**

Change both dashboard asset query strings from `v=30` to `v=31`.

- [ ] **Step 6: Run focused tests and JavaScript syntax verification**

Run:

```bash
uv run python -m unittest tests.test_dashboard_terminal
node --check orum/static/dashboard.js
```

Expected: all dashboard terminal tests pass and Node exits 0.

### Task 3: Verification and live delivery

**Files:**
- Verify only.

- [ ] **Step 1: Run the complete suite**

```bash
uv run python -m unittest discover -s tests
```

Expected: zero failures.

- [ ] **Step 2: Run structural checks**

```bash
uv run python -m compileall -q orum
git diff --check
```

Expected: both commands exit 0.

- [ ] **Step 3: Run GitNexus change detection**

Review the dashboard API and `renderMarketTerminal` flows; document the pre-existing critical dirty-worktree scope separately from this focused patch.

- [ ] **Step 4: Verify static live assets without a process restart**

```bash
curl -fsS http://127.0.0.1:8787/ | rg 'dashboard.js\?v=31'
curl -fsS http://127.0.0.1:8787/assets/dashboard.js | rg 'forecastMedianReturnAtHour'
```

The current HTTP handler reads assets per request, so the browser receives the new curve code after refresh. Do not restart the dashboard unless the operator separately approves loading the expanded 28-record Python API window.
