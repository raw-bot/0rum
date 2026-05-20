# Operator Dashboard Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real operator dashboard that shows what 0rum is doing in detail: active strategy, market candles, trade overlays, data freshness, and decision context.

**Architecture:** Keep the current FastAPI/Jinja dashboard and add focused dashboard data builders in `src/monitoring/dashboard_data.py` so `dashboard.py` does not become the dumping ground for chart queries. The UI remains local, read-only, no external CDN, and uses a native `<canvas>` candlestick chart refreshed by the existing `/api/dashboard` polling loop.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL, Jinja2, vanilla JavaScript, HTML canvas, pytest.

---

## Architecture

The current dashboard already exposes health, account context, trades, signals, and strategy stats from `src/monitoring/dashboard.py`. The missing operator information will be added as three API sections:

```json
{
  "market_data": {
    "instrument": "XAUUSD",
    "provider": "binance",
    "chart_timeframe": "M15",
    "last_price": 4527.23,
    "last_timestamp": "2026-05-20T20:00:00+00:00",
    "candles": [
      {
        "timestamp": "2026-05-20T19:45:00+00:00",
        "open": 4525.1,
        "high": 4529.4,
        "low": 4523.8,
        "close": 4527.23,
        "volume": 123
      }
    ]
  },
  "active_strategies": [
    {
      "strategy": "liquidity_sweep",
      "display_name": "Liquidity Sweep",
      "status": "active",
      "params": {
        "sweep_atr_mult": 0.7419080290994107,
        "sl_atr_mult": 0.44683370315192167,
        "tp_risk_mult": 1.742155984806974
      },
      "wfe": 1.8478,
      "profit_factor": 2.5744,
      "win_rate": 0.6296,
      "trade_count": 27,
      "validation_window": {
        "train_start": "2025-08-01T21:45:00+00:00",
        "test_end": "2026-04-10T21:45:00+00:00"
      }
    }
  ],
  "strategy_status": [
    {"strategy": "liquidity_sweep", "display_name": "Liquidity Sweep", "status": "active"},
    {"strategy": "trend_continuation", "display_name": "Trend Continuation", "status": "skipped_unvalidated"},
    {"strategy": "breakout_expansion", "display_name": "Breakout Expansion", "status": "skipped_unvalidated"},
    {"strategy": "ema_momentum", "display_name": "EMA Momentum", "status": "skipped_unvalidated"}
  ],
  "data_freshness": [
    {"timeframe": "M15", "rows": 62164, "last_timestamp": "2026-05-20T20:00:00+00:00", "age_minutes": 15.0, "status": "fresh"},
    {"timeframe": "H1", "rows": 15549, "last_timestamp": "2026-05-20T20:00:00+00:00", "age_minutes": 15.0, "status": "fresh"}
  ],
  "chart_overlays": [
    {
      "type": "open_trade",
      "direction": "SELL",
      "entry_price": 4527.23,
      "sl_price": 4553.28157,
      "tp1_price": 4481.8441,
      "tp2_price": 4436.4582,
      "status": "OPEN"
    }
  ]
}
```

The first implementation uses the existing 30-second polling loop. It is near-real-time from the bot database, not a WebSocket feed. The UI must make staleness visible so the operator can distinguish a quiet market from a broken scheduler.

## File Map

- Create: `src/monitoring/dashboard_data.py`
  - Owns market candle serialization, active strategy summaries, data freshness, and chart overlays.
  - Contains no FastAPI route definitions and no HTML.
- Modify: `src/monitoring/dashboard.py`
  - Calls the new data builders and merges their return values into `/api/dashboard`.
  - Keeps graceful degradation: failures add operational events and return empty sections.
- Modify: `src/templates/dashboard.html`
  - Adds a market chart section with `<canvas id="market-chart">`.
  - Adds active strategy and data freshness panels.
  - Adds vanilla JS renderers: `renderMarketData`, `renderStrategyOverview`, `renderDataFreshness`, and `drawCandleChart`.
- Create: `tests/test_monitoring/test_dashboard_data.py`
  - Unit tests for serialization and freshness classification.
- Modify: `tests/test_monitoring/test_dashboard.py`
  - API shape assertions for the new sections.
  - HTML assertions for the new UI containers and JS renderer names.

---

### Task 1: Add Data Builder Unit Tests

**Files:**
- Create: `tests/test_monitoring/test_dashboard_data.py`
- Create in Task 2: `src/monitoring/dashboard_data.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_monitoring/test_dashboard_data.py` with:

```python
"""Tests for dashboard market, strategy, and freshness data builders."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace


def test_serialize_candle_outputs_chart_ready_numbers():
    from src.monitoring.dashboard_data import serialize_candle

    row = SimpleNamespace(
        timestamp=datetime(2026, 5, 20, 20, 0, tzinfo=timezone.utc),
        open=Decimal("4520.10000"),
        high=Decimal("4530.25000"),
        low=Decimal("4510.75000"),
        close=Decimal("4527.23000"),
        volume=123,
    )

    assert serialize_candle(row) == {
        "timestamp": "2026-05-20T20:00:00+00:00",
        "open": 4520.1,
        "high": 4530.25,
        "low": 4510.75,
        "close": 4527.23,
        "volume": 123,
    }


def test_classify_freshness_uses_timeframe_specific_thresholds():
    from src.monitoring.dashboard_data import classify_freshness

    now = datetime(2026, 5, 20, 20, 30, tzinfo=timezone.utc)

    assert classify_freshness("M15", now - timedelta(minutes=20), now) == ("fresh", 20.0)
    assert classify_freshness("M15", now - timedelta(minutes=50), now) == ("stale", 50.0)
    assert classify_freshness("H1", now - timedelta(minutes=80), now) == ("fresh", 80.0)
    assert classify_freshness("H1", now - timedelta(minutes=150), now) == ("stale", 150.0)
    assert classify_freshness("D1", None, now) == ("missing", None)


def test_strategy_display_name_formats_known_strategy_ids():
    from src.monitoring.dashboard_data import strategy_display_name

    assert strategy_display_name("liquidity_sweep") == "Liquidity Sweep"
    assert strategy_display_name("trend_continuation") == "Trend Continuation"
    assert strategy_display_name("breakout_expansion") == "Breakout Expansion"
    assert strategy_display_name("ema_momentum") == "EMA Momentum"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
./.venv/bin/python -m pytest tests/test_monitoring/test_dashboard_data.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.monitoring.dashboard_data'`.

- [ ] **Step 3: Commit the failing tests is not allowed**

Do not commit after this step. Continue directly to Task 2 so the same commit contains red and green together.

---

### Task 2: Implement Dashboard Data Builders

**Files:**
- Create: `src/monitoring/dashboard_data.py`
- Test: `tests/test_monitoring/test_dashboard_data.py`

- [ ] **Step 1: Write minimal implementation for pure helpers**

Create `src/monitoring/dashboard_data.py` with:

```python
"""Data builders for the local operator dashboard.

This module keeps SQL/dashboard aggregation logic out of the FastAPI route.
It returns JSON-ready dictionaries only: no HTML and no FastAPI dependencies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.candle import Candle
from src.models.optimizer_result import OptimizerResultORM
from src.models.signal import ApprovedSignalORM, CandidateSignalORM
from src.models.trade import TradeORM


STRATEGY_ORDER = [
    "liquidity_sweep",
    "trend_continuation",
    "breakout_expansion",
    "ema_momentum",
]

FRESHNESS_LIMIT_MINUTES = {
    "M15": 45.0,
    "H1": 120.0,
    "H4": 360.0,
    "D1": 1800.0,
}


def strategy_display_name(strategy: str) -> str:
    names = {
        "liquidity_sweep": "Liquidity Sweep",
        "trend_continuation": "Trend Continuation",
        "breakout_expansion": "Breakout Expansion",
        "ema_momentum": "EMA Momentum",
    }
    return names.get(strategy, strategy.replace("_", " ").title())


def serialize_candle(row: Candle) -> dict[str, Any]:
    return {
        "timestamp": row.timestamp.isoformat(),
        "open": float(row.open),
        "high": float(row.high),
        "low": float(row.low),
        "close": float(row.close),
        "volume": int(row.volume),
    }


def classify_freshness(
    timeframe: str,
    last_timestamp: datetime | None,
    now: datetime | None = None,
) -> tuple[str, float | None]:
    if last_timestamp is None:
        return "missing", None
    if now is None:
        now = datetime.now(timezone.utc)
    if last_timestamp.tzinfo is None:
        last_timestamp = last_timestamp.replace(tzinfo=timezone.utc)
    age_minutes = round((now - last_timestamp.astimezone(timezone.utc)).total_seconds() / 60.0, 1)
    limit = FRESHNESS_LIMIT_MINUTES.get(timeframe, 120.0)
    return ("fresh" if age_minutes <= limit else "stale"), age_minutes
```

- [ ] **Step 2: Run helper tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_monitoring/test_dashboard_data.py -q
```

Expected: `3 passed`.

- [ ] **Step 3: Add async builder functions**

Append this code to `src/monitoring/dashboard_data.py`:

```python
async def build_market_data(
    db: AsyncSession,
    *,
    instrument: str,
    provider: str,
    timeframe: str = "M15",
    limit: int = 160,
) -> dict[str, Any]:
    stmt = (
        select(Candle)
        .where(
            Candle.instrument == instrument,
            Candle.timeframe == timeframe,
            Candle.complete.is_(True),
        )
        .order_by(Candle.timestamp.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = list(reversed(result.scalars().all()))
    candles = [serialize_candle(row) for row in rows]
    last = candles[-1] if candles else None
    return {
        "instrument": instrument,
        "provider": provider,
        "chart_timeframe": timeframe,
        "last_price": last["close"] if last else None,
        "last_timestamp": last["timestamp"] if last else None,
        "candles": candles,
    }


async def build_active_strategy_data(db: AsyncSession) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    stmt = (
        select(OptimizerResultORM)
        .where(OptimizerResultORM.is_active.is_(True))
        .order_by(OptimizerResultORM.created_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    active_by_strategy = {row.strategy: row for row in rows}

    active = []
    status = []
    for strategy in STRATEGY_ORDER:
        row = active_by_strategy.get(strategy)
        display_name = strategy_display_name(strategy)
        if row is None:
            status.append({
                "strategy": strategy,
                "display_name": display_name,
                "status": "skipped_unvalidated",
            })
            continue
        summary = {
            "strategy": strategy,
            "display_name": display_name,
            "status": "active",
            "params": dict(row.params),
            "wfe": float(row.wfe),
            "profit_factor": float(row.profit_factor) if row.profit_factor is not None else None,
            "win_rate": float(row.win_rate) if row.win_rate is not None else None,
            "trade_count": int(row.trade_count),
            "validation_window": {
                "train_start": row.train_start.isoformat(),
                "test_end": row.test_end.isoformat(),
            },
        }
        active.append(summary)
        status.append({
            "strategy": strategy,
            "display_name": display_name,
            "status": "active",
        })
    return active, status


async def build_data_freshness(db: AsyncSession, *, instrument: str) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    freshness = []
    for timeframe in ["M15", "H1", "H4", "D1"]:
        stmt = (
            select(func.count(Candle.id), func.max(Candle.timestamp))
            .where(
                Candle.instrument == instrument,
                Candle.timeframe == timeframe,
                Candle.complete.is_(True),
            )
        )
        result = await db.execute(stmt)
        rows, last_timestamp = result.one()
        status, age_minutes = classify_freshness(timeframe, last_timestamp, now)
        freshness.append({
            "timeframe": timeframe,
            "rows": int(rows or 0),
            "last_timestamp": last_timestamp.isoformat() if last_timestamp else None,
            "age_minutes": age_minutes,
            "status": status,
        })
    return freshness


async def build_chart_overlays(db: AsyncSession) -> list[dict[str, Any]]:
    stmt = (
        select(TradeORM, CandidateSignalORM.strategy)
        .join(ApprovedSignalORM, TradeORM.approved_signal_id == ApprovedSignalORM.id)
        .join(CandidateSignalORM, ApprovedSignalORM.candidate_signal_id == CandidateSignalORM.id)
        .where(TradeORM.status.in_(["OPEN", "TP1_HIT"]))
        .order_by(TradeORM.opened_at.desc())
    )
    result = await db.execute(stmt)
    overlays = []
    for trade, strategy in result.all():
        overlays.append({
            "type": "open_trade",
            "strategy": strategy,
            "direction": trade.direction,
            "entry_price": float(trade.entry_price),
            "sl_price": float(trade.trailing_stop_price or trade.sl_price),
            "tp1_price": float(trade.tp1_price),
            "tp2_price": float(trade.tp2_price) if trade.tp2_price is not None else None,
            "size_lots": float(trade.size_lots),
            "status": trade.status,
            "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
        })
    return overlays
```

- [ ] **Step 4: Run helper tests again**

Run:

```bash
./.venv/bin/python -m pytest tests/test_monitoring/test_dashboard_data.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit data builder module**

Run:

```bash
git add src/monitoring/dashboard_data.py tests/test_monitoring/test_dashboard_data.py
git commit -m "feat: add dashboard observability data builders"
```

Expected: commit succeeds.

---

### Task 3: Extend `/api/dashboard`

**Files:**
- Modify: `src/monitoring/dashboard.py`
- Modify: `tests/test_monitoring/test_dashboard.py`

- [ ] **Step 1: Add failing API shape test**

In `tests/test_monitoring/test_dashboard.py`, add this method inside `TestDashboardApi`:

```python
    def test_dashboard_api_observability_sections(self, client):
        """Response JSON exposes market, strategy, freshness, and chart overlay sections."""
        response = client.get("/api/dashboard")
        data = response.json()

        assert "market_data" in data
        assert "active_strategies" in data
        assert "strategy_status" in data
        assert "data_freshness" in data
        assert "chart_overlays" in data
        assert isinstance(data["market_data"], dict)
        assert isinstance(data["active_strategies"], list)
        assert isinstance(data["strategy_status"], list)
        assert isinstance(data["data_freshness"], list)
        assert isinstance(data["chart_overlays"], list)
```

- [ ] **Step 2: Run the new API test and verify it fails**

Run:

```bash
./.venv/bin/python -m pytest tests/test_monitoring/test_dashboard.py::TestDashboardApi::test_dashboard_api_observability_sections -q
```

Expected: FAIL because `"market_data"` is not present.

- [ ] **Step 3: Import builders in `dashboard.py`**

In `src/monitoring/dashboard.py`, add:

```python
from src.monitoring.dashboard_data import (
    build_active_strategy_data,
    build_chart_overlays,
    build_data_freshness,
    build_market_data,
)
```

- [ ] **Step 4: Merge observability sections into API response**

In `dashboard_api`, after the existing `result["account"] = ...` block, add:

```python
    # --- Operator Observability ---
    try:
        result["market_data"] = await build_market_data(
            db,
            instrument="XAUUSD",
            provider=settings.market_data_provider.value,
            timeframe="M15",
            limit=160,
        )
    except Exception as exc:
        log.warning("dashboard.market_data_failed", error=str(exc))
        result["market_data"] = {
            "instrument": "XAUUSD",
            "provider": settings.market_data_provider.value,
            "chart_timeframe": "M15",
            "last_price": None,
            "last_timestamp": None,
            "candles": [],
        }

    try:
        active_strategies, strategy_status = await build_active_strategy_data(db)
        result["active_strategies"] = active_strategies
        result["strategy_status"] = strategy_status
    except Exception as exc:
        log.warning("dashboard.active_strategies_failed", error=str(exc))
        result["active_strategies"] = []
        result["strategy_status"] = []

    try:
        result["data_freshness"] = await build_data_freshness(db, instrument="XAUUSD")
    except Exception as exc:
        log.warning("dashboard.data_freshness_failed", error=str(exc))
        result["data_freshness"] = []

    try:
        result["chart_overlays"] = await build_chart_overlays(db)
    except Exception as exc:
        log.warning("dashboard.chart_overlays_failed", error=str(exc))
        result["chart_overlays"] = []
```

- [ ] **Step 5: Run API/dashboard tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_monitoring/test_dashboard.py tests/test_monitoring/test_dashboard_data.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit API integration**

Run:

```bash
git add src/monitoring/dashboard.py tests/test_monitoring/test_dashboard.py
git commit -m "feat: expose dashboard observability data"
```

Expected: commit succeeds.

---

### Task 4: Add Dashboard UI Containers

**Files:**
- Modify: `src/templates/dashboard.html`
- Modify: `tests/test_monitoring/test_dashboard.py`

- [ ] **Step 1: Add failing HTML structure test**

In `tests/test_monitoring/test_dashboard.py`, add this method inside `TestDashboardPage`:

```python
    def test_dashboard_page_has_observability_ui_sections(self, client):
        """Dashboard page contains chart, strategy, and data freshness containers."""
        response = client.get("/dashboard")
        html = response.text

        assert 'id="market-chart"' in html
        assert 'id="market-summary"' in html
        assert 'id="active-strategy-container"' in html
        assert 'id="data-freshness-container"' in html
        assert "MARKET CHART" in html
        assert "ACTIVE STRATEGY" in html
        assert "DATA FRESHNESS" in html
```

- [ ] **Step 2: Run the HTML test and verify it fails**

Run:

```bash
./.venv/bin/python -m pytest tests/test_monitoring/test_dashboard.py::TestDashboardPage::test_dashboard_page_has_observability_ui_sections -q
```

Expected: FAIL because the containers do not exist.

- [ ] **Step 3: Add CSS for chart layout**

In `src/templates/dashboard.html`, after the existing `.card-full` CSS block, add:

```css
.market-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(320px, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.chart-shell {
  position: relative;
  width: 100%;
  height: 360px;
  border: 1px solid var(--border);
  background: #0a0c0e;
}

#market-chart {
  display: block;
  width: 100%;
  height: 100%;
}

.market-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 12px;
}

.summary-item {
  min-width: 120px;
}

.summary-label {
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.summary-value {
  margin-top: 4px;
  font-size: 14px;
}

.strategy-param-list,
.freshness-list {
  display: grid;
  gap: 8px;
}

.kv-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--border);
  padding: 6px 0;
}

.status-fresh { color: var(--accent); }
.status-stale,
.status-missing { color: var(--destructive); }

@media (max-width: 980px) {
  .market-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 4: Add HTML sections above open trades**

In `src/templates/dashboard.html`, after the status row and before the first existing `<div class="two-col-grid">`, add:

```html
<div class="market-grid">
  <div class="card">
    <div class="section-heading">MARKET CHART</div>
    <div id="market-summary" class="market-summary">
      <div class="summary-item">
        <div class="summary-label">Instrument</div>
        <div class="summary-value data-val" id="market-instrument">--</div>
      </div>
      <div class="summary-item">
        <div class="summary-label">Timeframe</div>
        <div class="summary-value data-val" id="market-timeframe">--</div>
      </div>
      <div class="summary-item">
        <div class="summary-label">Last Price</div>
        <div class="summary-value data-val" id="market-last-price">--</div>
      </div>
      <div class="summary-item">
        <div class="summary-label">Last Candle</div>
        <div class="summary-value data-val" id="market-last-timestamp">--</div>
      </div>
    </div>
    <div class="chart-shell">
      <canvas id="market-chart" width="960" height="360"></canvas>
    </div>
  </div>
  <div class="card">
    <div class="section-heading">ACTIVE STRATEGY</div>
    <div id="active-strategy-container">
      <div class="loading-text" id="active-strategy-loading">Loading...</div>
    </div>
    <div class="section-heading" style="margin-top: 18px;">DATA FRESHNESS</div>
    <div id="data-freshness-container">
      <div class="loading-text" id="data-freshness-loading">Loading...</div>
    </div>
  </div>
</div>
```

- [ ] **Step 5: Run HTML test**

Run:

```bash
./.venv/bin/python -m pytest tests/test_monitoring/test_dashboard.py::TestDashboardPage::test_dashboard_page_has_observability_ui_sections -q
```

Expected: PASS.

- [ ] **Step 6: Commit UI containers**

Run:

```bash
git add src/templates/dashboard.html tests/test_monitoring/test_dashboard.py
git commit -m "feat: add dashboard observability layout"
```

Expected: commit succeeds.

---

### Task 5: Render Strategy, Freshness, and Canvas Chart

**Files:**
- Modify: `src/templates/dashboard.html`
- Modify: `tests/test_monitoring/test_dashboard.py`

- [ ] **Step 1: Add failing static renderer test**

In `tests/test_monitoring/test_dashboard.py`, add this method inside `TestDashboardPage`:

```python
    def test_dashboard_page_has_observability_renderers(self, client):
        """Dashboard JS contains renderers for market chart and strategy context."""
        response = client.get("/dashboard")
        html = response.text

        assert "renderMarketData(data.market_data || {}, data.chart_overlays || [])" in html
        assert "renderStrategyOverview(data.active_strategies || [], data.strategy_status || [])" in html
        assert "renderDataFreshness(data.data_freshness || [])" in html
        assert "function drawCandleChart(canvas, candles, overlays)" in html
        assert "function priceToY(price, minPrice, maxPrice, chartTop, chartHeight)" in html
```

- [ ] **Step 2: Run renderer test and verify it fails**

Run:

```bash
./.venv/bin/python -m pytest tests/test_monitoring/test_dashboard.py::TestDashboardPage::test_dashboard_page_has_observability_renderers -q
```

Expected: FAIL because renderer functions are missing.

- [ ] **Step 3: Wire renderers into `renderDashboard`**

In `src/templates/dashboard.html`, update `renderDashboard(data)` to include the new renderers before `renderOpenTrades(...)`:

```javascript
function renderDashboard(data) {
  renderHeader(data);
  renderStatusRow(data);
  renderOperationalEvents(data.operational_events || []);
  renderMarketData(data.market_data || {}, data.chart_overlays || []);
  renderStrategyOverview(data.active_strategies || [], data.strategy_status || []);
  renderDataFreshness(data.data_freshness || []);
  renderOpenTrades(data.open_trades || []);
  renderClosedTrades(data.closed_trades || []);
  renderCandidateSignals(data.candidate_signals || []);
  renderStrategyStats(data.strategy_stats || []);
  renderLatestSignals(data.latest_signals || []);
}
```

- [ ] **Step 4: Add market data renderer**

In `src/templates/dashboard.html`, after `renderStatusRow(data)`, add:

```javascript
function renderMarketData(marketData, overlays) {
  document.getElementById('market-instrument').textContent = marketData.instrument || '--';
  document.getElementById('market-timeframe').textContent = marketData.chart_timeframe || '--';
  document.getElementById('market-last-price').textContent = formatNumber(marketData.last_price, 2);
  document.getElementById('market-last-timestamp').textContent =
    marketData.last_timestamp ? marketData.last_timestamp.substring(11, 19) + ' UTC' : '--';

  const canvas = document.getElementById('market-chart');
  drawCandleChart(canvas, marketData.candles || [], overlays || []);
}
```

- [ ] **Step 5: Add strategy overview renderer**

In `src/templates/dashboard.html`, after `renderMarketData`, add:

```javascript
function renderStrategyOverview(activeStrategies, strategyStatus) {
  const container = document.getElementById('active-strategy-container');
  const loading = document.getElementById('active-strategy-loading');
  if (loading) loading.style.display = 'none';

  if (!activeStrategies || activeStrategies.length === 0) {
    renderEmptyState(container, 'No active validated strategy');
    return;
  }

  const strategy = activeStrategies[0];
  const wrapper = document.createElement('div');
  const title = document.createElement('div');
  title.className = 'summary-value data-val';
  title.textContent = strategy.display_name;
  wrapper.appendChild(title);

  const metrics = document.createElement('div');
  metrics.className = 'strategy-param-list';
  appendKeyValue(metrics, 'Status', strategy.status.toUpperCase(), 'status-fresh');
  appendKeyValue(metrics, 'WFE', formatNumber(strategy.wfe, 4), 'data-val');
  appendKeyValue(metrics, 'Profit Factor', formatNumber(strategy.profit_factor, 2), 'data-val');
  appendKeyValue(metrics, 'Win Rate', formatPercent(strategy.win_rate, 1), 'data-val');
  appendKeyValue(metrics, 'Trades', strategy.trade_count, 'data-val');

  const params = strategy.params || {};
  for (const key of Object.keys(params).sort()) {
    appendKeyValue(metrics, key, formatNumber(params[key], 4), 'data-val');
  }
  wrapper.appendChild(metrics);

  const statusList = document.createElement('div');
  statusList.className = 'strategy-param-list';
  statusList.style.marginTop = '12px';
  for (const item of strategyStatus || []) {
    const statusClass = item.status === 'active' ? 'status-fresh' : 'status-stale';
    appendKeyValue(statusList, item.display_name, item.status, statusClass);
  }
  wrapper.appendChild(statusList);

  container.replaceChildren(wrapper);
}
```

- [ ] **Step 6: Add freshness renderer**

In `src/templates/dashboard.html`, after `renderStrategyOverview`, add:

```javascript
function renderDataFreshness(items) {
  const container = document.getElementById('data-freshness-container');
  const loading = document.getElementById('data-freshness-loading');
  if (loading) loading.style.display = 'none';

  if (!items || items.length === 0) {
    renderEmptyState(container, 'No candle freshness data');
    return;
  }

  const list = document.createElement('div');
  list.className = 'freshness-list';
  for (const item of items) {
    const age = item.age_minutes === null || item.age_minutes === undefined
      ? '--'
      : formatNumber(item.age_minutes, 1) + 'm';
    const value = item.status.toUpperCase() + ' · ' + age + ' · ' + item.rows + ' rows';
    appendKeyValue(list, item.timeframe, value, 'status-' + item.status);
  }
  container.replaceChildren(list);
}
```

- [ ] **Step 7: Add key-value helper**

In `src/templates/dashboard.html`, before `renderEmptyState`, add:

```javascript
function appendKeyValue(container, label, value, valueClass) {
  const row = document.createElement('div');
  row.className = 'kv-row';
  const key = document.createElement('span');
  key.textContent = label;
  key.style.color = 'var(--text-2)';
  const val = document.createElement('span');
  val.className = valueClass || '';
  val.textContent = value === null || value === undefined ? '--' : String(value);
  row.appendChild(key);
  row.appendChild(val);
  container.appendChild(row);
  return row;
}
```

- [ ] **Step 8: Add chart drawing helpers**

In `src/templates/dashboard.html`, before `formatNumber`, add:

```javascript
function drawCandleChart(canvas, candles, overlays) {
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  const width = Math.max(320, Math.floor(rect.width || canvas.width));
  const height = Math.max(240, Math.floor(rect.height || canvas.height));
  canvas.width = Math.floor(width * scale);
  canvas.height = Math.floor(height * scale);
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#0a0c0e';
  ctx.fillRect(0, 0, width, height);

  if (!candles || candles.length === 0) {
    ctx.fillStyle = '#5a6272';
    ctx.font = '13px system-ui, sans-serif';
    ctx.fillText('No candles available', 16, 28);
    return;
  }

  const chartLeft = 48;
  const chartRight = 16;
  const chartTop = 16;
  const chartBottom = 28;
  const chartWidth = width - chartLeft - chartRight;
  const chartHeight = height - chartTop - chartBottom;
  const allPrices = [];
  for (const c of candles) {
    allPrices.push(Number(c.high), Number(c.low));
  }
  for (const overlay of overlays || []) {
    allPrices.push(
      Number(overlay.entry_price),
      Number(overlay.sl_price),
      Number(overlay.tp1_price),
      Number(overlay.tp2_price)
    );
  }
  const finitePrices = allPrices.filter(Number.isFinite);
  const minRaw = Math.min(...finitePrices);
  const maxRaw = Math.max(...finitePrices);
  const padding = Math.max((maxRaw - minRaw) * 0.08, 1);
  const minPrice = minRaw - padding;
  const maxPrice = maxRaw + padding;

  ctx.strokeStyle = '#252a31';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = chartTop + (chartHeight / 4) * i;
    ctx.beginPath();
    ctx.moveTo(chartLeft, y);
    ctx.lineTo(width - chartRight, y);
    ctx.stroke();
    const price = maxPrice - ((maxPrice - minPrice) / 4) * i;
    ctx.fillStyle = '#8a939f';
    ctx.font = '11px SF Mono, monospace';
    ctx.fillText(price.toFixed(2), 4, y + 4);
  }

  const step = chartWidth / candles.length;
  const candleWidth = Math.max(2, Math.min(8, step * 0.6));
  candles.forEach((c, index) => {
    const x = chartLeft + index * step + step / 2;
    const open = Number(c.open);
    const high = Number(c.high);
    const low = Number(c.low);
    const close = Number(c.close);
    const yOpen = priceToY(open, minPrice, maxPrice, chartTop, chartHeight);
    const yHigh = priceToY(high, minPrice, maxPrice, chartTop, chartHeight);
    const yLow = priceToY(low, minPrice, maxPrice, chartTop, chartHeight);
    const yClose = priceToY(close, minPrice, maxPrice, chartTop, chartHeight);
    const up = close >= open;
    ctx.strokeStyle = up ? '#00c896' : '#e05252';
    ctx.fillStyle = up ? '#00c896' : '#e05252';
    ctx.beginPath();
    ctx.moveTo(x, yHigh);
    ctx.lineTo(x, yLow);
    ctx.stroke();
    const bodyTop = Math.min(yOpen, yClose);
    const bodyHeight = Math.max(1, Math.abs(yClose - yOpen));
    ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
  });

  drawOverlays(ctx, overlays || [], minPrice, maxPrice, chartTop, chartHeight, chartLeft, width - chartRight);
}

function priceToY(price, minPrice, maxPrice, chartTop, chartHeight) {
  const numeric = Number(price);
  if (!Number.isFinite(numeric)) return chartTop + chartHeight / 2;
  return chartTop + ((maxPrice - numeric) / (maxPrice - minPrice)) * chartHeight;
}

function drawOverlays(ctx, overlays, minPrice, maxPrice, chartTop, chartHeight, x1, x2) {
  for (const overlay of overlays) {
    drawPriceLine(ctx, overlay.entry_price, 'ENTRY ' + overlay.direction, '#d4a017', minPrice, maxPrice, chartTop, chartHeight, x1, x2);
    drawPriceLine(ctx, overlay.sl_price, 'SL', '#e05252', minPrice, maxPrice, chartTop, chartHeight, x1, x2);
    drawPriceLine(ctx, overlay.tp1_price, 'TP1', '#00c896', minPrice, maxPrice, chartTop, chartHeight, x1, x2);
    if (overlay.tp2_price !== null && overlay.tp2_price !== undefined) {
      drawPriceLine(ctx, overlay.tp2_price, 'TP2', '#00c896', minPrice, maxPrice, chartTop, chartHeight, x1, x2);
    }
  }
}

function drawPriceLine(ctx, price, label, color, minPrice, maxPrice, chartTop, chartHeight, x1, x2) {
  const numeric = Number(price);
  if (!Number.isFinite(numeric)) return;
  const y = priceToY(numeric, minPrice, maxPrice, chartTop, chartHeight);
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(x1, y);
  ctx.lineTo(x2, y);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.font = '11px SF Mono, monospace';
  ctx.fillText(label + ' ' + numeric.toFixed(2), x1 + 6, y - 4);
}
```

- [ ] **Step 9: Run renderer and dashboard tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_monitoring/test_dashboard.py -q
```

Expected: all dashboard tests pass.

- [ ] **Step 10: Commit renderers**

Run:

```bash
git add src/templates/dashboard.html tests/test_monitoring/test_dashboard.py
git commit -m "feat: render operator chart and strategy context"
```

Expected: commit succeeds.

---

### Task 6: Runtime Verification and Browser QA

**Files:**
- No source changes expected unless verification reveals a defect.

- [ ] **Step 1: Run focused tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_monitoring/test_dashboard.py tests/test_monitoring/test_dashboard_data.py -q
```

Expected: all focused tests pass.

- [ ] **Step 2: Run full tests**

Run:

```bash
./.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Compile Python files**

Run:

```bash
./.venv/bin/python -m compileall -q src scripts tests
```

Expected: exit code `0`.

- [ ] **Step 4: Check whitespace**

Run:

```bash
git diff --check
```

Expected: no output and exit code `0`.

- [ ] **Step 5: Rebuild local app**

Run:

```bash
docker compose up -d --build
```

Expected: app, postgres, and redis containers are running and healthy.

- [ ] **Step 6: Verify API payload live**

Run:

```bash
curl -s http://localhost:8000/api/dashboard
```

Expected: response includes `"market_data"`, `"active_strategies"`, `"strategy_status"`, `"data_freshness"`, and `"chart_overlays"`. `market_data.candles` contains at least one candle when the local DB has candle rows.

- [ ] **Step 7: Verify browser UI**

Open:

```text
http://localhost:8000/dashboard
```

Expected visible UI:
- `MARKET CHART` section renders a non-empty candlestick chart.
- Chart includes horizontal lines for open trade entry, SL, TP1, and TP2 when open trades exist.
- `ACTIVE STRATEGY` shows `Liquidity Sweep`, WFE, profit factor, win rate, trade count, and active params.
- `DATA FRESHNESS` shows `M15`, `H1`, `H4`, and `D1` with `FRESH`, `STALE`, or `MISSING`.
- Existing open trades, latest signals, health, equity, and risk panels still render.

- [ ] **Step 8: Commit verification fixes if any were needed**

If source changes were made during verification, run:

```bash
git add src/monitoring/dashboard_data.py src/monitoring/dashboard.py src/templates/dashboard.html tests/test_monitoring/test_dashboard.py tests/test_monitoring/test_dashboard_data.py
git commit -m "fix: polish dashboard observability view"
```

Expected: commit succeeds only if there are actual verification fixes.

---

## Expected Final State

- `/api/dashboard` exposes enough detail for an operator to understand the bot’s current behavior.
- `/dashboard` shows:
  - account equity and risk per trade,
  - candle chart from the bot’s DB,
  - open trade overlays,
  - active strategy name and validated parameters,
  - WFE, profit factor, win rate, trade count,
  - skipped strategy statuses,
  - freshness of M15/H1/H4/D1 data.
- No external JavaScript libraries or CDN dependencies are introduced.
- The UI remains local and read-only.
- The scheduler/data distinction is visible: stale candles appear as an operator warning instead of silently looking normal.

## Verification Commands

Run all commands from repo root:

```bash
./.venv/bin/python -m pytest tests/test_monitoring/test_dashboard.py tests/test_monitoring/test_dashboard_data.py -q
./.venv/bin/python -m pytest -q
./.venv/bin/python -m compileall -q src scripts tests
git diff --check
docker compose up -d --build
curl -s http://localhost:8000/api/dashboard
```

## Self-Review

- Spec coverage: the plan covers strategy visibility, chart visibility, data freshness, chart overlays, API payloads, HTML rendering, and runtime verification.
- Placeholder scan: this plan contains concrete file paths, concrete payload shapes, concrete test code, concrete implementation snippets, and exact commands.
- Type consistency: `market_data`, `active_strategies`, `strategy_status`, `data_freshness`, and `chart_overlays` are introduced in Task 3 and consumed by the UI in Task 5 with the same field names.
