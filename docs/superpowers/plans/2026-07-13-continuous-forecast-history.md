# Continuous Forecast History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-origin yellow/white audit overlay with one continuous yellow +24-hour forecast history aligned to actual candle timestamps.

**Architecture:** Extend the pure walk-forward evaluator to expose the latest 168 matured +24-hour p50 predictions without temporal leakage. Merge matured live journal points over reconstructed points in the forecast-history module, pass the resulting series through the dashboard API, and render exactly one historical SVG polyline while leaving the current future fan unchanged.

**Tech Stack:** Python 3.11, unittest/pytest, vanilla JavaScript, SVG, CSS.

**Execution note:** Work inline on `strategy/backtest-parity` because this is the path served by the approved local dashboard. Do not stage unrelated dirty-worktree changes and do not restart any live process without the user’s explicit confirmation.

---

### Task 1: Produce an honest +24-hour walk-forward history

**Files:**
- Modify: `orum/portfolio/forecast_gate.py:80-147`
- Test: `tests/test_forecast_gate.py`

- [ ] **Step 1: Write failing alignment and no-leakage tests**

Add tests that require a `history_24h` list, a 24-hour target offset, a maximum of 168 points, and prediction stability when only the future target close is changed:

```python
def test_walk_forward_report_exposes_last_seven_days_at_fixed_24h_target():
    candles = _market()
    report = walk_forward_forecast(candles, min_evaluations=80, neighbour_count=40)
    history = report["history_24h"]
    assert len(history) == 168
    assert history[-1]["target_ts"] == "2023-12-16T13:13:20+00:00"
    assert history[-1]["origin_ts"] == "2023-12-15T13:13:20+00:00"
    self.assertAlmostEqual(
        history[-1]["predicted_price"],
        history[-1]["origin_price"] * (1 + history[-1]["median_return"]),
    )

def test_walk_forward_history_prediction_does_not_use_its_future_target():
    original = _market()
    changed = [dict(row) for row in original]
    changed[-1]["close"] *= 1.5
    before = walk_forward_forecast(original, min_evaluations=80, neighbour_count=40)["history_24h"][-1]
    after = walk_forward_forecast(changed, min_evaluations=80, neighbour_count=40)["history_24h"][-1]
    self.assertAlmostEqual(after["predicted_price"], before["predicted_price"])
    assert after["actual_price"] != before["actual_price"]
```

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_forecast_gate.py::ForecastGateTests::test_walk_forward_report_exposes_last_seven_days_at_fixed_24h_target \
  tests/test_forecast_gate.py::ForecastGateTests::test_walk_forward_history_prediction_does_not_use_its_future_target
```

Expected: failure because `history_24h` does not exist.

- [ ] **Step 3: Build the trace from existing chronological evaluations**

In `walk_forward_forecast`, keep the existing training/evaluation loop and collect only horizon 24 points:

```python
history_24h = []
# inside the evaluation loop, after predicted and actual are computed
if horizon == 24:
    target_index = eval_origin + horizon
    origin_price = closes[eval_origin]
    median_return = predicted["p50"]
    history_24h.append({
        "origin_ts": _iso_timestamp(valid[eval_origin]["ts"]),
        "target_ts": _iso_timestamp(valid[target_index]["ts"]),
        "origin_price": origin_price,
        "predicted_price": origin_price * (1 + median_return),
        "actual_price": closes[target_index],
        "median_return": median_return,
        "median_error": actual - median_return,
        "source": "walk_forward",
    })
# in the returned report
"history_24h": history_24h[-168:],
```

Do not change `_available_rows`, `_conditional_quantiles`, qualification, or decision logic.

- [ ] **Step 4: Run the focused forecast tests and confirm GREEN**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_gate.py`

Expected: all tests pass.

---

### Task 2: Prefer matured live archive points and expose the series

**Files:**
- Modify: `orum/portfolio/forecast_history.py`
- Modify: `orum/dashboard.py:1012-1106`
- Test: `tests/test_forecast_history.py`
- Test: `tests/test_dashboard_terminal.py`

- [ ] **Step 1: Write failing live-priority and API tests**

Add a merge test with a reconstructed point and a matured +24-hour journal point sharing the same target. Require the live point to replace it:

```python
merged = merge_forecast_history_24h(
    [{"target_ts": "2026-07-14T06:00:00+00:00", "predicted_price": 101,
      "actual_price": 102, "source": "walk_forward"}],
    [prediction, realization_24h],
    strategy_id="btc_ak_macd_4h",
)
assert merged == [{
    "origin_ts": "2026-07-13T06:00:00+00:00",
    "target_ts": "2026-07-14T06:00:00+00:00",
    "origin_price": 100.0,
    "predicted_price": 103.0,
    "actual_price": 104.0,
    "median_return": 0.03,
    "median_error": 0.01,
    "source": "live_archive",
}]
```

Extend the strict strategy-scoping dashboard test so the UT Bot payload contains only its calibrated row’s `forecast_history_24h`.

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_forecast_history.py tests/test_dashboard_terminal.py \
  -k 'merge_forecast_history_24h or continuous_forecast_history'
```

Expected: import/key failures because the merge function and API field do not exist.

- [ ] **Step 3: Implement the merge and API field**

Add `merge_forecast_history_24h(reconstructed, records, strategy_id, limit=168)` to `forecast_history.py`. Key reconstructed points by normalized `target_ts`; for each composed prediction with a realized `24` row, derive nominal target time as `origin_ts + timedelta(hours=24)`, compute the frozen p50 predicted price, and overwrite that key with `source="live_archive"`. Return chronological values limited to 168.

In both dashboard success and error payloads add:

```python
"forecast_history_24h": merge_forecast_history_24h(
    calibrated.get("history_24h") or [],
    forecast_history_records,
    strategy_id=strategy_id,
),
```

Keep the append-only journal and existing `forecast_history` field unchanged for audit compatibility.

- [ ] **Step 4: Run the focused data/API tests and confirm GREEN**

Run: `.venv/bin/python -m pytest -q tests/test_forecast_history.py tests/test_dashboard_terminal.py`

Expected: all tests pass before the UI-removal assertions are introduced.

---

### Task 3: Replace the old overlay with one yellow historical line

**Files:**
- Modify: `orum/static/dashboard.js:834-895, 1055-1145`
- Modify: `orum/static/dashboard.css:217-220`
- Modify: `orum/static/dashboard.html:90`
- Test: `tests/test_dashboard_terminal.py`

- [ ] **Step 1: Replace old UI tests with failing single-line assertions**

Delete tests for progressive per-origin segments and median-error labels. Add a Node-backed test for the new path builder and static assertions:

```python
def test_continuous_forecast_history_is_one_yellow_target_aligned_path(self):
    result = self._run_forecast_history_path(
        [{"target_ts": 1_780_003_600_000, "predicted_price": 101},
         {"target_ts": 1_780_007_200_000, "predicted_price": 102}],
        candles,
    )
    self.assertEqual(result, [
        {"ts": 1_780_003_600_000, "price": 101},
        {"ts": 1_780_007_200_000, "price": 102},
    ])

def test_old_yellow_white_audit_overlay_is_removed(self):
    self.assertNotIn("buildForecastAuditSegments", js)
    self.assertNotIn("forecast-audit-realized", js + css)
    self.assertNotIn("JAUNE 50% = PRÉVU · BLANC = RÉEL", js)
    self.assertEqual(js.count('class="forecast-history-line"'), 1)
    self.assertIn(".forecast-history-line", css)
    self.assertIn('/assets/dashboard.js?v=32', html)
```

- [ ] **Step 2: Run the UI tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_dashboard_terminal.py \
  -k 'continuous_forecast_history or old_yellow_white'
```

Expected: failure because the old overlay is still present and v31 is still served.

- [ ] **Step 3: Implement the single path and remove the old renderer**

Replace `buildForecastAuditSegments` and `renderForecastAudit` with a pure target-aligned builder:

```javascript
function buildForecastHistoryPath(history, candles) {
  if (!candles.length) return [];
  const firstTs = Number(candles[0].ts), lastTs = Number(candles.at(-1).ts);
  return (history || []).map(row => ({
    ts: new Date(row.target_ts).getTime(),
    price: Number(row.predicted_price),
  })).filter(point => Number.isFinite(point.ts) && point.ts >= firstTs
    && point.ts <= lastTs && point.price > 0).sort((a, b) => a.ts - b.ts);
}
```

Map each history point to `nearestCandleIndex(candles, point.ts)` and render exactly one `<polyline class="forecast-history-line">`; include its prices in the visible y-scale, remove every old audit/error/legend call, and change the rail copy to `Historique prévision +24 h · N points`. Update the chart ARIA label to mention the +24-hour historical forecast.

Replace the three old CSS selectors with:

```css
.terminal-svg .forecast-history-line {
  fill: none;
  stroke: var(--warn);
  stroke-width: 1.15;
  opacity: .72;
  vector-effect: non-scaling-stroke;
}
```

Bump only the JavaScript asset to v32. Do not alter the future calibrated fan, milestone nodes, zoom, pan, event pins, candles, volume, or momentum.

- [ ] **Step 4: Run syntax, UI, and adjacent regression tests**

Run:

```bash
node --check orum/static/dashboard.js
.venv/bin/python -m pytest -q \
  tests/test_forecast_gate.py tests/test_forecast_history.py \
  tests/test_dashboard_terminal.py tests/test_dashboard_state.py
```

Expected: all tests pass.

- [ ] **Step 5: Verify the served dashboard without changing trading state**

After obtaining restart confirmation only if Python code is not picked up automatically, verify:

```bash
curl -fsS http://127.0.0.1:8787/ | rg 'dashboard.js\?v=32'
curl -fsS http://127.0.0.1:8787/assets/dashboard.js | \
  rg 'forecast-history-line|Historique prévision \+24 h'
curl -fsS http://127.0.0.1:8787/api/state
```

Require non-empty `forecast_history_24h`, one yellow path with at least two points, `worker.running == true`, and unchanged paper positions/fills.
