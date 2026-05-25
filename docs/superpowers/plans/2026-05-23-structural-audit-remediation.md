# 0rum Structural Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the structural audit findings that make sizing, P&L, risk gates, trade lifecycle, and backtesting materially unreliable.

**Architecture:** Put all money math behind explicit instrument specs and account-state services, then make the live signal-mode monitor, risk gates, dashboard, and optimizer consume the same units. The plan fixes critical financial correctness first, then closes operational gaps, then removes sources of drift and dead code.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL, Redis, Pydantic v2, pytest, structlog.

---

## Audit Coverage Map

- F-01, F-02, F-14: Tasks 1 and 3 centralize XAUUSD contract size and add exposure gates.
- F-03, F-04, F-15: Tasks 2, 3, and 4 make P&L and daily-loss checks equity-based.
- F-05, F-17, F-21, F-22: Task 5 fixes trade lifecycle, expiry, and monitor session ownership.
- F-06, F-07, F-09, F-10: Task 6 aligns backtesting with sizing, spread/slippage, and robustness checks.
- F-08: Task 7 keeps Binance/PAXG as runtime plumbing only and makes optimizer data-source constraints explicit.
- F-11, F-12, F-13, F-16: Tasks 3 and 8 add drawdown, kill switch, concentration, and aggregate risk controls.
- F-18, F-19, F-20, F-27: Task 8 covers local API protections and database initialization hygiene.
- F-23, F-24, F-25, F-26, F-28: Task 9 removes duplicated indicators, dead simulator code, and brittle optimizer parameter handling.

## File Structure

- Create: `src/market/instruments.py`
  - Owns `InstrumentSpec` and `INSTRUMENT_SPECS`; no database or network access.
- Create: `src/risk/account.py`
  - Owns equity, exposure, daily P&L, and drawdown query helpers for gates and sizing.
- Modify: `src/config.py`
  - Adds risk configuration for max leverage, stop-risk cap, drawdown breaker, trade expiry, spread, slippage, and kill switch.
- Modify: `src/risk/events.py`
  - Extends DTOs with notional, exposure, equity, and rejection details.
- Modify: `src/risk/sizer.py`
  - Replaces hardcoded `Decimal("100")` with instrument spec.
- Modify: `src/risk/gates.py`
  - Replaces price-return daily P&L with equity-return and aggregate exposure gates.
- Modify: `src/risk/runner.py`
  - Reads dynamic paper equity and applies all new gates before approving size.
- Modify: `src/execution/paper/account.py`
  - Uses instrument spec and preserves equity-based P&L semantics.
- Modify: `src/execution/paper/service.py`
  - Provides dynamic equity and exposure for risk runner and dashboard.
- Modify: `src/models/trade.py`
  - Adds `equity_at_open`, `notional_usd`, `risk_amount_usd`, and `expired_at` columns.
- Create: `alembic/versions/0006_trade_risk_accounting.py`
  - Adds new trade accounting columns and indexes.
- Modify: `src/scheduler/jobs.py`
  - Fixes close P&L, trade expiry, and single-session monitor updates.
- Modify: `src/pipeline/runner.py`
  - Persists equity, notional, and risk accounting values when a paper trade is created.
- Modify: `src/backtesting/walk_forward.py`
  - Removes dead simulator and adds execution-cost aware signal-mode simulation.
- Modify: `src/backtesting/optimizer.py`
  - Scores in account-return units, includes sizing and robustness penalties.
- Create: `src/backtesting/execution_costs.py`
  - Encapsulates spread/slippage price adjustments for simulation.
- Modify: `src/ingestion/market_client.py`
  - Logs the PAXG proxy explicitly as runtime-only plumbing.
- Modify: `src/monitoring/dashboard.py`, `src/monitoring/health.py`, `src/templates/dashboard.html`
  - Exposes corrected account/risk state, expiry, kill switch state, and provider warnings.
- Create: `src/monitoring/auth.py`
  - Adds optional local dashboard token dependency and simple Redis-backed rate limit helper.
- Create: `src/indicators/atr.py`, `src/indicators/ema.py`
  - Shared indicator implementations.
- Modify tests under `tests/test_risk/`, `tests/test_execution/`, `tests/test_monitoring/`, `tests/test_backtesting/`, `tests/test_ingestion/`, and `tests/test_config/`.

---

### Task 1: Instrument Specs And Contract Size

**Files:**
- Create: `src/market/instruments.py`
- Create: `src/market/__init__.py`
- Modify: `src/config.py`
- Modify: `src/risk/sizer.py`
- Modify: `src/execution/paper/account.py`
- Test: `tests/test_market/test_instruments.py`
- Test: `tests/test_risk/test_sizer.py`
- Test: `tests/test_execution/test_paper_account.py`

- [x] **Step 1: Write failing tests for XAUUSD contract size**

```python
from decimal import Decimal

from src.market.instruments import get_instrument_spec


def test_xauusd_contract_size_is_named_and_decimal():
    spec = get_instrument_spec("XAUUSD")
    assert spec.instrument == "XAUUSD"
    assert spec.contract_size == Decimal("100")
    assert spec.price_precision == Decimal("0.01")
```

Run: `pytest tests/test_market/test_instruments.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.market'`.

- [x] **Step 2: Add instrument spec module**

```python
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class InstrumentSpec:
    instrument: str
    contract_size: Decimal
    price_precision: Decimal
    default_spread_usd: Decimal
    default_slippage_usd: Decimal


INSTRUMENT_SPECS = {
    "XAUUSD": InstrumentSpec(
        instrument="XAUUSD",
        contract_size=Decimal("100"),
        price_precision=Decimal("0.01"),
        default_spread_usd=Decimal("0.30"),
        default_slippage_usd=Decimal("0.10"),
    )
}


def get_instrument_spec(instrument: str) -> InstrumentSpec:
    key = instrument.upper()
    try:
        return INSTRUMENT_SPECS[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported instrument: {instrument}") from exc
```

- [x] **Step 3: Replace hardcoded contract-size math**

In `src/risk/sizer.py`, import `get_instrument_spec`, add an `instrument: str = "XAUUSD"` parameter, and replace:

```python
size_lots = (risk_amount / (sl_distance * Decimal("100"))).quantize(Decimal("0.01"))
```

with:

```python
spec = get_instrument_spec(instrument)
size_lots = (risk_amount / (sl_distance * spec.contract_size)).quantize(Decimal("0.01"))
```

In `src/execution/paper/account.py`, replace every `Decimal("100")` multiplier with `get_instrument_spec("XAUUSD").contract_size`.

- [x] **Step 4: Run focused tests**

Run: `pytest tests/test_market/test_instruments.py tests/test_risk/test_sizer.py tests/test_execution/test_paper_account.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Deferred: Task 1 passed spec and quality review, but was not committed because preexisting untracked paper-account files overlap the Task 1 write set.

```bash
git add src/market src/config.py src/risk/sizer.py src/execution/paper/account.py tests/test_market tests/test_risk/test_sizer.py tests/test_execution/test_paper_account.py
git commit -m "fix: centralize XAUUSD contract specification"
```

---

### Task 2: Trade Accounting Schema

**Files:**
- Modify: `src/models/trade.py`
- Create: `alembic/versions/0006_trade_risk_accounting.py`
- Test: `tests/test_models_datetime.py`
- Test: `tests/test_execution/test_paper_account.py`

- [x] **Step 1: Add ORM fields**

Add these fields to `TradeORM`:

```python
equity_at_open: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
notional_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
risk_amount_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
expired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [x] **Step 2: Add migration**

```python
"""add trade risk accounting columns

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("equity_at_open", sa.Numeric(12, 2), nullable=True))
    op.add_column("trades", sa.Column("notional_usd", sa.Numeric(14, 2), nullable=True))
    op.add_column("trades", sa.Column("risk_amount_usd", sa.Numeric(12, 2), nullable=True))
    op.add_column("trades", sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("idx_trades_expired_at", "trades", ["expired_at"])


def downgrade() -> None:
    op.drop_index("idx_trades_expired_at", table_name="trades")
    op.drop_column("trades", "expired_at")
    op.drop_column("trades", "risk_amount_usd")
    op.drop_column("trades", "notional_usd")
    op.drop_column("trades", "equity_at_open")
```

- [x] **Step 3: Run model and migration smoke tests**

Run: `python3 -m py_compile src/models/trade.py alembic/versions/0006_trade_risk_accounting.py`
Expected: PASS with no output.

Run: `pytest tests/test_models_datetime.py tests/test_execution/test_paper_account.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

Deferred: Task 2 passed spec and quality review, but was not committed because migration `0006` depends on preexisting untracked migration `0005`.

```bash
git add src/models/trade.py alembic/versions/0006_trade_risk_accounting.py tests/test_models_datetime.py tests/test_execution/test_paper_account.py
git commit -m "fix: persist trade risk accounting fields"
```

---

### Task 3: Dynamic Equity And Aggregate Risk Gates

**Files:**
- Create: `src/risk/account.py`
- Modify: `src/config.py`
- Modify: `src/risk/events.py`
- Modify: `src/risk/gates.py`
- Modify: `src/risk/runner.py`
- Modify: `src/execution/paper/service.py`
- Test: `tests/test_risk/test_gates.py`
- Test: `tests/test_risk/test_runner.py`
- Test: `tests/test_config/test_settings.py`

- [x] **Step 1: Add settings**

Add to `Settings`:

```python
max_account_leverage: Decimal = Decimal("10")
max_stop_risk_pct: Decimal = Decimal("0.05")
max_equity_drawdown_pct: Decimal = Decimal("0.10")
concentration_reduce_at: int = 2
concentration_block_at: int = 4
```

- [x] **Step 2: Write failing gate tests**

Add tests that assert:

```python
assert passed is False
assert reason == "max_account_leverage"
```

for a post-trade exposure above `max_account_leverage`, and:

```python
assert daily_pnl_pct == -0.031
assert passed is False
```

when closed `TradeORM.pnl` totals `-310` against `equity_at_open=10000`.

- [x] **Step 3: Implement account helpers**

`src/risk/account.py` should expose:

```python
async def get_current_equity(session, starting_balance: Decimal) -> Decimal
async def get_daily_equity_pnl_pct(session, fallback_equity: Decimal) -> Decimal
async def get_open_exposure(session) -> tuple[Decimal, Decimal]
async def get_max_drawdown_pct(session, starting_balance: Decimal) -> Decimal
```

Use `src.execution.paper.service.compute_current_paper_state()` for equity. For daily P&L, query `sum(TradeORM.pnl)` for trades closed today and divide by `coalesce(avg(TradeORM.equity_at_open), fallback_equity)`.

- [x] **Step 4: Update risk runner ordering**

`RiskGateRunner.evaluate()` must:

1. Read dynamic equity.
2. Run daily loss using equity-return.
3. Run max positions.
4. Run drawdown gate.
5. Count same-direction open positions.
6. Size with dynamic equity.
7. Compute new notional and stop risk using `InstrumentSpec.contract_size`.
8. Reject if aggregate leverage or stop risk exceeds configured cap.
9. Reject if same-direction count is at or above `concentration_block_at`.

- [x] **Step 5: Extend DTOs**

Add these optional fields to `RiskDecision`:

```python
equity_usd: Optional[Decimal] = None
notional_after_usd: Optional[Decimal] = None
stop_risk_after_usd: Optional[Decimal] = None
exposure_multiple_after: Optional[Decimal] = None
candidate_notional_usd: Optional[Decimal] = None
```

- [x] **Step 6: Run focused risk tests**

Run: `pytest tests/test_risk/test_gates.py tests/test_risk/test_runner.py tests/test_config/test_settings.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

Deferred: Task 3 passed spec and quality review, but was not committed because the workspace still contains overlapping preexisting dirty/untracked work from Tasks 1-2 and earlier paper-account changes.

```bash
git add src/config.py src/risk/account.py src/risk/events.py src/risk/gates.py src/risk/runner.py src/execution/paper/service.py tests/test_risk tests/test_config/test_settings.py
git commit -m "fix: gate trades on dynamic equity and exposure"
```

---

### Task 4: Equity-Based P&L In Live Signal Monitoring

**Files:**
- Modify: `src/scheduler/jobs.py`
- Modify: `src/pipeline/runner.py`
- Modify: `src/execution/paper/account.py`
- Modify: `src/execution/paper/service.py`
- Modify: `src/monitoring/dashboard.py`
- Test: `tests/test_monitoring/test_monitor_trades.py`
- Test: `tests/test_monitoring/test_dashboard.py`
- Test: `tests/test_execution/test_paper_account.py`

- [x] **Step 1: Write failing monitor tests**

Create a closed BUY trade with:

```python
entry_price = Decimal("3300")
exit_price = Decimal("3295")
size_lots = Decimal("0.20")
equity_at_open = Decimal("10000")
```

Expected:

```python
pnl = Decimal("-100.00")
pnl_pct = Decimal("-0.01000")
```

- [x] **Step 2: Fix close formula**

In `_close_trade()`, compute USD P&L first:

```python
contract_size = get_instrument_spec("XAUUSD").contract_size
full_final_usd = (exit_price - entry_price) * direction_sign * trade.size_lots * contract_size
tp1_usd = (tp1_price - entry_price) * direction_sign * trade.size_lots * Decimal("0.5") * contract_size
final_half_usd = (exit_price - entry_price) * direction_sign * trade.size_lots * Decimal("0.5") * contract_size
pnl_usd = full_final_usd if close_reason == "SL" else tp1_usd + final_half_usd
equity_base = Decimal(str(trade.equity_at_open or settings.theoretical_equity_usd))
trade.pnl = pnl_usd.quantize(Decimal("0.01"))
trade.pnl_pct = (pnl_usd / equity_base).quantize(Decimal("0.00001"))
```

- [x] **Step 3: Update strategy stats units**

Keep `StrategyStatsORM.total_pnl_pct`, `gross_profit_pct`, and `gross_loss_pct` as account-return percentages after this task. Use `trade.pnl_pct`, not price-return values.

- [x] **Step 4: Ensure trade creation stores accounting values**

Where `TradeORM` is created from a risk-approved signal, set:

```python
equity_at_open=risk_decision.equity_usd
notional_usd=risk_decision.candidate_notional_usd
risk_amount_usd=risk_decision.sizing.risk_amount_usd
```

- [x] **Step 5: Run focused tests**

Run: `pytest tests/test_monitoring/test_monitor_trades.py tests/test_monitoring/test_dashboard.py tests/test_execution/test_paper_account.py tests/test_pipeline/test_runner.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

Deferred: Task 4 passed spec and quality review, but was not committed because the workspace still contains overlapping dirty/untracked work from earlier tasks.

```bash
git add src/scheduler/jobs.py src/pipeline/runner.py src/execution/paper src/monitoring/dashboard.py tests/test_monitoring tests/test_execution/test_paper_account.py tests/test_pipeline/test_runner.py
git commit -m "fix: store paper pnl as equity return"
```

---

### Task 5: Trade Expiry And Monitor Session Ownership

**Files:**
- Modify: `src/config.py`
- Modify: `src/scheduler/jobs.py`
- Modify: `src/models/trade.py`
- Test: `tests/test_monitoring/test_monitor_trades.py`

- [x] **Step 1: Add expiry settings**

```python
trade_expiry_hours: int = 72
```

- [x] **Step 2: Write failing expiry test**

Create an `OPEN` trade with `opened_at = now - timedelta(hours=73)`, no SL or TP hit, run `monitor_trades()`, and assert:

```python
assert trade.status == "CLOSED"
assert trade.close_reason == "EXPIRED"
assert trade.expired_at is not None
```

- [x] **Step 3: Refactor monitor update flow**

Change `_process_trade()` and `_close_trade()` to receive the active `AsyncSession` used by `monitor_trades()` instead of opening nested sessions. All status changes for one trade should occur in the same transaction that loaded the trade.

- [x] **Step 4: Implement expiry close**

Before price-touch checks:

```python
if datetime.now(timezone.utc) - trade.opened_at >= timedelta(hours=settings.trade_expiry_hours):
    await _close_trade(session, trade, strategy_name, "EXPIRED", mark_price, entry, tp1, direction_sign)
    return
```

Use the latest M15 close as `mark_price`. `EXPIRED` should not call `breaker.record_stop()` unless P&L is negative and the team later decides to treat expiry as a stop; this plan keeps breaker semantics stop-only.

- [x] **Step 5: Run monitor tests**

Run: `pytest tests/test_monitoring/test_monitor_trades.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

Deferred: Task 5 passed spec and quality review, but was not committed because the workspace still contains overlapping dirty/untracked work from earlier tasks.

```bash
git add src/config.py src/models/trade.py src/scheduler/jobs.py tests/test_monitoring/test_monitor_trades.py
git commit -m "fix: expire unresolved paper trades"
```

---

### Task 6: Backtest Parity With Costs, Sizing, And Robustness

**Files:**
- Create: `src/backtesting/execution_costs.py`
- Modify: `src/backtesting/walk_forward.py`
- Modify: `src/backtesting/optimizer.py`
- Test: `tests/test_backtesting/test_walk_forward.py`
- Test: `tests/test_backtesting/test_optimizer.py`
- Test: `tests/test_backtesting/test_optimizer_integration.py`

- [x] **Step 1: Add execution-cost tests**

Assert BUY entries are worse by half-spread plus slippage and SELL entries are worse in the opposite direction:

```python
assert adjust_entry("BUY", Decimal("3300"), Decimal("0.30"), Decimal("0.10")) == Decimal("3300.25")
assert adjust_entry("SELL", Decimal("3300"), Decimal("0.30"), Decimal("0.10")) == Decimal("3299.75")
```

- [x] **Step 2: Implement execution cost helpers**

`src/backtesting/execution_costs.py`:

```python
from decimal import Decimal


def adjust_entry(direction: str, price: Decimal, spread: Decimal, slippage: Decimal) -> Decimal:
    adjustment = spread / Decimal("2") + slippage
    return price + adjustment if direction == "BUY" else price - adjustment


def adjust_exit(direction: str, price: Decimal, spread: Decimal, slippage: Decimal) -> Decimal:
    adjustment = spread / Decimal("2") + slippage
    return price - adjustment if direction == "BUY" else price + adjustment
```

- [x] **Step 3: Remove dead simulator**

Delete `simulate_trade_outcome()` from `src/backtesting/walk_forward.py` and update tests/imports to use `simulate_signal_mode_trade_outcome()` only.

- [x] **Step 4: Return account-return P&L from simulator**

Change `simulate_signal_mode_trade_outcome()` to accept:

```python
size_lots: Decimal
equity_at_open: Decimal
contract_size: Decimal
spread_usd: Decimal
slippage_usd: Decimal
```

Return `pnl_usd / equity_at_open`, matching live `TradeORM.pnl_pct`.

- [x] **Step 5: Integrate simplified sizing in optimizer**

For each simulated signal, size against a rolling simulated equity using the same `calculate_position_size()` and current ATR percentile. Start each run at `settings.theoretical_equity_usd`, update rolling equity by each simulated `pnl_usd`, and pass the resulting account-return series to PF/WFE/Monte Carlo.

- [x] **Step 6: Add robustness penalty**

For the top 5 combos, evaluate one neighbor per parameter at `value +/- 10% of range`, clamped to `PARAM_RANGES`. Reject activation when fewer than 3 of those neighbors have OOS PF above 1.0.

- [x] **Step 7: Run backtesting tests**

Run: `pytest tests/test_backtesting/test_walk_forward.py tests/test_backtesting/test_optimizer.py tests/test_backtesting/test_optimizer_integration.py -v`
Expected: PASS.

- [ ] **Step 8: Commit deferred**

```bash
git add src/backtesting tests/test_backtesting
git commit -m "fix: align optimizer pnl with signal-mode accounting"
```

Deferred because the workspace contains pre-existing dirty and untracked work
outside this task; Task 6 has passed spec review, quality review, py_compile,
diff check, and the expanded focused suite:

```bash
./.venv/bin/pytest tests/test_backtesting/test_walk_forward.py tests/test_backtesting/test_optimizer.py tests/test_backtesting/test_optimizer_integration.py tests/test_monitoring/test_monitor_trades.py -v
# 72 passed
```

---

### Task 7: Provider Boundary For Runtime Proxy Versus Research Data

**Files:**
- Modify: `src/ingestion/market_client.py`
- Modify: `src/backtesting/historical_loader.py`
- Modify: `src/backtesting/optimizer.py`
- Modify: `docs/decisions/ADR-003-dukascopy-research-ingestion.md`
- Test: `tests/test_ingestion/test_market_client.py`
- Test: `tests/test_backtesting/test_optimizer.py`

- [x] **Step 1: Write provider boundary tests**

Assert `MarketDataClient` logs or exposes `source_kind="runtime_proxy"` for Binance/PAXG and optimizer diagnostics include `research_source="dukascopy"` when fed Dukascopy candles.

- [x] **Step 2: Make proxy status explicit**

In `MarketDataClient.get_candles()`, include:

```python
source_kind="runtime_proxy"
proxy_symbol=symbol
canonical_instrument=instrument
```

in logs and returned diagnostic metadata where available.

- [x] **Step 3: Guard optimizer activation**

Before activating optimizer params, require a research-source marker that is not `runtime_proxy`. If the candle source cannot be proven research-grade, write diagnostics and skip activation.

- [x] **Step 4: Run provider tests**

Run: `pytest tests/test_ingestion/test_market_client.py tests/test_backtesting/test_optimizer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit deferred**

```bash
git add src/ingestion/market_client.py src/backtesting docs/decisions/ADR-003-dukascopy-research-ingestion.md tests/test_ingestion/test_market_client.py tests/test_backtesting/test_optimizer.py
git commit -m "fix: enforce research data boundary for optimizer"
```

Deferred because the workspace contains pre-existing dirty and untracked work
outside this task. Task 7 passed spec review, quality review, py_compile,
diff check, and the expanded focused suite:

```bash
./.venv/bin/pytest tests/test_data/test_dukascopy_importer.py tests/test_ingestion/test_market_client.py tests/test_ingestion/test_candle_fetcher.py tests/test_backtesting/test_optimizer.py tests/test_models_datetime.py -v
# 50 passed
```

---

### Task 8: Kill Switch, Drawdown Breaker, And Local API Protection

**Files:**
- Create: `src/monitoring/auth.py`
- Modify: `src/config.py`
- Modify: `src/risk/breaker.py`
- Modify: `src/risk/runner.py`
- Modify: `src/monitoring/dashboard.py`
- Modify: `src/monitoring/health.py`
- Modify: `src/main.py`
- Test: `tests/test_monitoring/test_health_risk.py`
- Test: `tests/test_monitoring/test_dashboard.py`
- Test: `tests/test_risk/test_runner.py`

- [x] **Step 1: Add settings**

```python
dashboard_token: str = ""
dashboard_rate_limit_per_minute: int = 120
kill_switch_redis_key: str = "risk:kill_switch"
```

- [x] **Step 2: Add kill switch checks**

`RiskGateRunner.evaluate()` should check Redis key `risk:kill_switch` after breaker state and reject with `reason="kill_switch_active"` when set.

- [x] **Step 3: Add local API endpoints**

Add authenticated endpoints:

```python
POST /api/kill
POST /api/resume
```

`/api/kill` sets the Redis key to `"1"`. `/api/resume` deletes it. These endpoints should require `dashboard_token` when configured and return HTTP 403 when the token is missing or incorrect.

- [x] **Step 4: Add drawdown breaker**

Use `get_max_drawdown_pct()` from `src/risk/account.py`. Reject new trades when drawdown is greater than or equal to `settings.max_equity_drawdown_pct` with `reason="max_equity_drawdown"`.

- [x] **Step 5: Add rate limit dependency**

In `src/monitoring/auth.py`, implement a Redis-backed fixed-window counter keyed by client IP and endpoint. Apply it to `/health`, `/dashboard`, and `/api/dashboard`.

- [x] **Step 6: Run API and risk tests**

Run: `pytest tests/test_monitoring/test_health_risk.py tests/test_monitoring/test_dashboard.py tests/test_risk/test_runner.py -v`
Expected: PASS.

- [ ] **Step 7: Commit deferred**

```bash
git add src/config.py src/risk src/monitoring src/main.py tests/test_monitoring tests/test_risk/test_runner.py
git commit -m "fix: add kill switch and drawdown protections"
```

Deferred because the workspace contains pre-existing dirty and untracked work
outside this task. Task 8 passed py_compile and the focused suite:

```bash
./.venv/bin/pytest tests/test_monitoring/test_health_risk.py tests/test_monitoring/test_dashboard.py tests/test_risk/test_runner.py -v
# 49 passed
```

---

### Task 9: Indicator Consolidation And Optimizer Hygiene

**Files:**
- Create: `src/indicators/__init__.py`
- Create: `src/indicators/atr.py`
- Create: `src/indicators/ema.py`
- Modify: `src/strategies/base.py`
- Modify: `src/backtesting/regime_detector.py`
- Modify: `src/backtesting/walk_forward.py`
- Modify: `src/strategies/ema_momentum.py`
- Modify: `src/strategies/trend_continuation.py`
- Modify: `src/strategies/runner.py`
- Test: `tests/test_strategies/test_base.py`
- Test: `tests/test_pipeline/test_regime_detector.py`
- Test: `tests/test_strategies/test_ema_momentum.py`
- Test: `tests/test_strategies/test_trend_continuation.py`
- Test: `tests/test_strategies/test_runner.py`

- [x] **Step 1: Add shared ATR and EMA tests**

Assert one known candle sequence returns the same ATR from strategy base, regime detector, and walk-forward after consolidation. Assert EMA output length and final value match the existing implementation for a fixed close-price list.

- [x] **Step 2: Implement shared indicators**

`src/indicators/atr.py` should expose:

```python
def true_ranges(candles: list) -> list[float]
def atr_wilder(candles: list, period: int = 14) -> float
def atr_sma(candles: list, period: int = 14) -> float
```

Use `atr_wilder()` for trading logic and document `atr_sma()` only for diagnostics.

`src/indicators/ema.py` should expose:

```python
def ema(values: list[float], period: int) -> list[float]
```

- [x] **Step 3: Replace duplicate implementations**

Update strategy, regime, and walk-forward modules to import shared indicator helpers. Remove local `_calculate_atr` and `_ema` copies.

- [x] **Step 4: Validate optimizer params**

In `src/strategies/runner.py`, add:

```python
def validate_params_against_ranges(params: dict, ranges: dict[str, tuple[float, float]]) -> dict:
    validated = {}
    for name, (lo, hi) in ranges.items():
        value = float(params[name])
        if value < lo or value > hi:
            raise ValueError(f"Parameter {name}={value} outside range [{lo}, {hi}]")
        validated[name] = value
    return validated
```

Call it when loading active optimizer params. Initialize `optimizer_result_count = 0` before the query branch to remove the latent unbound variable risk.

- [x] **Step 5: Run strategy and regime tests**

Run: `pytest tests/test_strategies tests/test_pipeline/test_regime_detector.py -v`
Expected: PASS.

- [ ] **Step 6: Commit deferred**

```bash
git add src/indicators src/strategies src/backtesting/regime_detector.py src/backtesting/walk_forward.py tests/test_strategies tests/test_pipeline/test_regime_detector.py
git commit -m "refactor: share indicators and validate optimizer params"
```

Deferred because the workspace contains pre-existing dirty and untracked work
outside this task. Task 9 passed py_compile, diff check, and the focused suite:

```bash
./.venv/bin/pytest tests/test_strategies tests/test_pipeline/test_regime_detector.py -v
# 95 passed
```

---

### Task 10: Full Verification And Documentation

**Files:**
- Modify: `audit_structurel_0rum.md`
- Create: `docs/decisions/ADR-005-risk-accounting-and-backtest-parity.md`
- Modify: `.env.example`
- Modify: `AGENTS.md` if the project-level source of truth needs the new risk settings.

- [x] **Step 1: Add ADR**

Document these decisions:

- `TradeORM.pnl_pct` means account return, not price return.
- `TradeORM.pnl` is USD P&L.
- `equity_at_open` is captured on trade creation and used for realized return.
- Risk gates use dynamic paper equity in signal mode.
- Optimizer scores use the same account-return unit as live theoretical monitoring.
- Binance/PAXG remains runtime plumbing only; strategy validation uses Dukascopy XAUUSD.

- [x] **Step 2: Update env example**

Add:

```env
MAX_ACCOUNT_LEVERAGE=10
MAX_STOP_RISK_PCT=0.05
MAX_EQUITY_DRAWDOWN_PCT=0.10
CONCENTRATION_REDUCE_AT=2
CONCENTRATION_BLOCK_AT=4
TRADE_EXPIRY_HOURS=72
SPREAD_USD=0.30
SLIPPAGE_USD=0.10
DASHBOARD_TOKEN=
DASHBOARD_RATE_LIMIT_PER_MINUTE=120
```

- [x] **Step 3: Run full verification**

Run:

```bash
python3 -m py_compile src/**/*.py tests/**/*.py
pytest -q
```

Expected: `pytest` exits 0.

- [x] **Step 4: Update audit status**

Append a remediation section to `audit_structurel_0rum.md` with a table mapping F-01 through F-28 to the commits or tasks that fixed them.

- [ ] **Step 5: Commit deferred**

```bash
git add audit_structurel_0rum.md docs/decisions/ADR-005-risk-accounting-and-backtest-parity.md .env.example AGENTS.md
git commit -m "docs: record structural audit remediation decisions"
```

Deferred because the workspace contains pre-existing dirty and untracked work
outside this task. Task 10 passed py_compile and full verification:

```bash
./.venv/bin/pytest -q
# 379 passed
```

---

## Execution Order

1. Task 1 must land before any financial math changes.
2. Task 2 must land before Tasks 3 through 5 because they persist new accounting values.
3. Tasks 3, 4, and 5 should be implemented as one review batch if using subagents, because they touch the same risk and monitor contracts.
4. Task 6 should start only after Task 4 passes, so optimizer parity targets the corrected live semantics.
5. Tasks 7 through 9 can run after Task 6 or in parallel in separate worktrees if the branch is split.
6. Task 10 is last.

## Verification Gates

- Focused tests after every task.
- Full `pytest -q` after Tasks 5, 6, 9, and 10.
- Manual dashboard smoke test after Task 8:
  - Start app.
  - Open `/dashboard`.
  - Confirm equity, notional exposure, stop risk, kill switch state, and provider warning render.
  - Hit `/api/kill`, confirm new risk decisions reject.
  - Hit `/api/resume`, confirm rejection clears.

## Self-Review

- Spec coverage: all 28 audit findings are mapped in the coverage table.
- Red-flag scan: no deferred implementation markers are present; every task has concrete files, commands, and expected results.
- Type consistency: `Decimal` is used for money and contract-size math; `pnl_pct` is consistently defined as account return; optimizer and live monitoring converge on the same unit.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-23-structural-audit-remediation.md`. Two execution options:

1. Subagent-Driven (recommended) - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. Inline Execution - execute tasks in this session using executing-plans, batch execution with checkpoints.
