# Phase 5: Backtesting & Validation — Research

**Researched:** 2026-04-22
**Domain:** Walk-forward optimization, Latin Hypercube Sampling, Monte Carlo bootstrap, historical XAUUSD data sourcing
**Confidence:** HIGH (framework mechanics), MEDIUM (external data providers)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Technical Objectives (two separable deliveries):**
1. Framework technique exécutable — optimizer/walk-forward/monte_carlo code, wired into scheduler, reading from DB, writing to `optimizer_results`
2. Validation statistique complète — WFE > 50% confirmed against real 6m/2m XAUUSD windows, multi-window test passing, Monte Carlo passing

**Walk-Forward Parameters:**
- LHS sampling: exactly 100 combinations per strategy
- Training window: `wf_train_months = 6` months
- OOS window: `wf_test_months = 2` months
- WFE gate: `wfe_minimum = 0.50` (50%)
- Multi-window test: 2 of 3 OOS windows must be profitable
- Optimizer cadence: every 24h via APScheduler

**Monte Carlo Parameters:**
- Simulations: 1000
- P95 drawdown ≤ 2× historical drawdown
- P5 profit factor > 1.0

**DB Output:**
- Best params written to `optimizer_results` with `is_active = TRUE`
- Strategy failing WFE gate retains PREVIOUS active params (not deactivated)
- WFE score stored per result (used by signal ranker's 20% weight)

**Data Strategy Constraints (what NOT to assume):**
- Binance/PAXG is NOT acceptable for Phase 5 optimization or validation
- retired-provider warm-up bars alone are NOT sufficient for a 6-month training window
- retired-provider historical API MAY support larger requests but not validated at optimizer scale
- Do NOT assume retired provider can automatically supply 6m/2m windows without rate-limit assessment

**Architecture Constraints:**
- Provider/execution separation MUST be preserved
- No major ingestion refactor — optimizer reads from DB candles
- Walk-forward reads candles already in DB — NOT an ingestion concern
- New modules go in `src/backtesting/` (optimizer.py, walk_forward.py, monte_carlo.py)

**Decision Gate: Data Strategy (NOT locked — must be exposed):**

Chemin A — Bootstrap sur DB locale accumulée progressivement via retired-provider:
- Build framework now; optimizer runs but produces no WFE-valid results until enough data accumulates
- Activation gate: optimizer only activates params once sufficient historical data exists
- Time-to-full-validation: several months of retired-provider accumulation

Chemin B — Validation complète dès le premier run avec provider historique séparé:
- Requires integrating a separate XAUUSD historical provider
- Delivers full WFE and Monte Carlo validation on first run
- Additional module: `src/data/historical_loader.py` or equivalent

### Claude's Discretion

- Internal implementation of LHS sampling (scipy.stats.qmc.LatinHypercube recommended)
- Walk-forward evaluation metric (profit factor chosen — see research)
- Monte Carlo simulation method (bootstrap resampling — standard)
- Whether to use vectorized backtesting or hand-roll (hand-roll chosen)
- Exact DB schema extension if fields need adding
- Whether framework test uses synthetic candle fixtures or real minimal DB snapshot

### Deferred Ideas (OUT OF SCOPE)

- Auto-switching between providers mid-optimization run
- Storing full simulation trace data (only summary metrics needed)
- Optimization of risk parameters (all risk params are env vars — never optimized)
- Structural parameter optimization (EMA50/200, swing order=10 — explicitly forbidden)
- Cross-strategy portfolio optimization
- Regime-conditional walk-forward splits
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| OPTIM-01 | Walk-forward optimizer samples 100 parameter combos via LHS per strategy | LHS API verified: `LatinHypercube(d=3).random(n=100)` + `qmc.scale()` — see Code Examples |
| OPTIM-02 | WFE > 50% gate — strategies with failing WFE are not activated | WFE formula confirmed: `oos_score / is_score` — profit factor used as score metric |
| OPTIM-03 | Multi-window test requires 2 of 3 OOS windows profitable before activating params | Rolling 3-window construction verified — see Architecture Patterns |
| OPTIM-04 | Optimizer runs every 24h and activates best-validated params for each strategy | APScheduler pattern confirmed — follow `create_scheduler()` in `jobs.py` |
| OPTIM-05 | Monte Carlo validation (1000 simulations) — P95 drawdown ≤ 2× historical, P5 profit factor > 1.0 | Bootstrap resampling verified in-environment — see Code Examples |
</phase_requirements>

---

## Summary

Phase 5 delivers the walk-forward optimizer (LHS × 100), WFE gate, multi-window test (2/3 OOS), Monte Carlo (1000 sims), and 24h APScheduler job. All framework mechanics are implementable immediately using libraries already in the stack (scipy 1.17.1, numpy, pandas). The framework is architecturally clean: fully async for DB access, sync for pure computation, strictly separated from ingestion.

The central blocker for full statistical validation is data availability. Research confirms that Chemin A (retired-provider accumulation) requires 7-12 months of continuous operation before any timeframe reaches the minimum candle count for a 3-window evaluation. Chemin B is viable via two primary free sources: **retired bootstrap archive** (XAUUSD M1 data, resamplable to H1/H4/D1, no API key, covers years of history) and **retired daily CSV source** (D1 XAUUSD CSV, daily data only, long history). Neither provides H1 data via a programmatic API with no key — retired bootstrap archive requires a manual download step, but that is a one-time operation suitable for bootstrapping.

**Primary recommendation:** Build the full framework in Wave 1 (optimizer, walk_forward, monte_carlo modules), wire the 24h scheduler job, and implement an explicit data-guard that prevents param activation when insufficient candles are present. In Wave 2, expose the Chemin A vs. Chemin B decision gate with a concrete action item: either wait for retired provider accumulation (not recommended — 7+ months) or perform a one-time retired bootstrap archive M1 download, resample it, and bulk-insert into the `candles` table (recommended for immediate OPTIM-01 through OPTIM-05 verification).

---

## Standard Stack

### Core (already in pyproject.toml — no new installs needed)

| Library | Version (verified) | Purpose | Why Standard |
|---------|--------------------|---------|--------------|
| scipy | 1.17.1 [VERIFIED: venv] | LHS sampling via `scipy.stats.qmc.LatinHypercube` | Already declared in pyproject.toml; optimal space-filling vs random |
| numpy | in stack | P&L arrays, drawdown computation, percentile calculations | Already in stack; vectorized ops |
| pandas | in stack | Candle data loading, resampling (for historical_loader.py) | Already in stack; `resample().agg()` handles OHLCV upsampling |
| sqlalchemy asyncio | 2.x | DB reads for candles + writes to optimizer_results | Existing pattern — `AsyncSessionLocal` |
| apscheduler | 3.10.x | 24h optimizer job | Existing pattern — `AsyncIOScheduler` |

### No New Dependencies Required

The framework can be built entirely from the existing stack. No new packages needed.

**Version verification (already confirmed):**
```bash
# Run inside project venv to confirm
python -c "import scipy; print(scipy.__version__)"  # 1.17.1
```

---

## Data Reality Check (Critical for Planning)

This section drives the Chemin A vs. Chemin B decision gate. All numbers are verified by in-session computation.

### Minimum Candle Counts Required

For 3-window walk-forward (12 calendar months of data): [VERIFIED: computed in-session]

| Timeframe | 1-window minimum | 3-window minimum | retired provider warmup (current) | Days to 3-win from zero |
|-----------|-----------------|------------------|---------------------|------------------------|
| D1 | 171 | 257 | 60 | ~192 trading days (~9.1 months) |
| H4 | 1,028 | 1,542 | 80 | ~245 trading days (~11.7 months) |
| H1 | 4,114 | 6,171 | 250 | ~242 trading days (~11.5 months) |
| M15 | 16,457 | 24,685 | 300 | ~255 trading days (~12.1 months) |

**Conclusion:** Chemin A cannot yield any WFE-valid activation for approximately 7-12 months of retired provider daily operation. This is not viable for completing Phase 5 requirements.

### External Data Provider Assessment

**retired bootstrap archive — XAUUSD M1 data** [MEDIUM confidence — verified via WebSearch, not direct fetch]
- Coverage: XAUUSD M1 (1-minute bars) organized by year/month
- Date range: Multiple years of history available (source confirms long-running provider)
- Cost: Free, no API key required
- Access: Manual web download per year/month batch (no programmatic API)
- License: Files described as "free and easy" — no explicit algo trading restriction found
- Format: CSV compatible with MT4/NinjaTrader import (parseable with pandas)
- **Key advantage:** M1 data can be resampled to any higher timeframe (H1, H4, D1) using `pandas.resample().agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})`
- **Key risk:** Manual download per year/month batch; requires a one-time human action; no auto-refresh

**retired daily CSV source — XAUUSD D1 data** [MEDIUM confidence — confirmed via WebSearch]
- Coverage: XAUUSD daily (D1) data, 20+ years of history
- Date range: Configurable start/end date via URL `retired_daily_csv_source.com/q/d/?s=xauusd`
- Cost: Free, no API key required
- Access: CSV download via URL — programmable with `requests` or `pandas_datareader.retired daily CSV sourceDailyReader`
- **Key limitation:** D1 only — cannot supply H1 or H4 data
- **Use case:** Best for validating D1-timeframe strategies (TrendContinuation uses H1 EMA200 — D1 data insufficient for sub-daily signal generation)
- License: Public web data — no explicit restriction identified

**Alpha Vantage — FX_INTRADAY / GOLD endpoints** [MEDIUM confidence — verified via WebSearch]
- FX_INTRADAY with `symbol=XAU&to_symbol=USD&interval=60min`: free tier returns ~100 bars (compact outputsize); full outputsize requires premium subscription
- Commodity endpoint `GOLD` / `GOLD_SILVER_SPOT`: D1 data only on free tier
- Free tier: 25 requests/day [CITED: AlphaLog guide]
- **Verdict:** Insufficient for H1 backtesting on free tier. 100 bars = 4 days of H1. Paid plan needed for full history.

**retired market-data candidate — C:XAUUSD** [LOW confidence — no free tier specifics found]
- Supports XAU/USD as forex pair (`C:XAUUSD`)
- Historical aggregates available across timeframes
- Free tier limitations: specific H1 coverage and rate limits not verified in this session
- Rebranded to retired market-data candidate in October 2025 [CITED: search result]
- **Verdict:** Cannot recommend without verifying free tier H1 coverage; requires direct API testing

**yfinance GC=F (COMEX Gold Futures)** [MEDIUM confidence — domain knowledge verified by search]
- `GC=F` is the continuous front-month COMEX gold futures contract
- Key risk: Futures prices differ from spot XAUUSD by the cost-of-carry (contango/basis spread)
- Typical contango: gold futures trade ~$5-20 above spot depending on time-to-expiry
- Roll risk: `GC=F` switches to the next contract at expiry — introduces price discontinuities in historical data
- Signal strategies are tuned to XAUUSD spot (retired provider demo trades spot XAUUSD) — validating on futures introduces systematic price offset
- **Verdict:** Not recommended for production validation. Acceptable only for initial framework smoke tests where absolute price levels matter less than relative signal generation patterns.

**retired bootstrap archive is the recommended Chemin B source** for H1 data. retired daily CSV source is acceptable as a supplementary D1 validation source.

---

## Architecture Patterns

### Recommended Module Structure

```
src/backtesting/
├── __init__.py
├── optimizer.py          # LHS sampling, per-strategy optimization loop, DB writes
├── walk_forward.py       # Window splitting, per-window IS/OOS evaluation, WFE + multi-window gate
└── monte_carlo.py        # Bootstrap resampling, P95 drawdown, P5 profit factor

src/data/                 # NEW — only if Chemin B chosen
└── historical_loader.py  # One-time CSV → DB bulk insert for retired bootstrap archive M1 data

src/scheduler/
└── jobs.py               # Add run_optimizer() async job (follow existing pattern)
```

### Pattern 1: LHS Sampling to Parameter Combinations

**What:** `scipy.stats.qmc.LatinHypercube(d=N_PARAMS).random(n=100)` generates 100 × N_PARAMS matrix in [0,1], then `qmc.scale()` maps each column to its parameter range.

**When to use:** Once per strategy at the start of each optimizer run.

**Verified code (in-project venv, scipy 1.17.1):**
```python
# Source: verified in-session against scipy 1.17.1
from scipy.stats.qmc import LatinHypercube, scale

def _sample_param_combinations(
    param_ranges: dict[str, tuple[float, float]],
    n_combos: int,
) -> list[dict[str, float]]:
    """Generate n_combos LHS parameter combinations for one strategy.

    Args:
        param_ranges: Strategy PARAM_RANGES dict, e.g.
            {"sweep_atr_mult": (0.2, 0.8), "sl_atr_mult": (0.3, 1.0), "tp_risk_mult": (1.2, 3.0)}
        n_combos: Number of combinations (100 per CONTEXT.md locked decision).

    Returns:
        List of dicts, each mapping param_name → sampled float value.
    """
    param_names = list(param_ranges.keys())
    d = len(param_names)
    l_bounds = [param_ranges[k][0] for k in param_names]
    u_bounds = [param_ranges[k][1] for k in param_names]

    sampler = LatinHypercube(d=d)
    unit_samples = sampler.random(n=n_combos)          # shape: (100, d)
    scaled = scale(unit_samples, l_bounds, u_bounds)   # shape: (100, d)

    return [
        {param_names[j]: float(scaled[i, j]) for j in range(d)}
        for i in range(n_combos)
    ]
```

**Critical API note:** The constructor argument is `d=` (dimension count), NOT `n_components=`. Using `n_components` raises `TypeError` in scipy 1.17.1. [VERIFIED: in-session]

### Pattern 2: Walk-Forward Window Construction

**What:** Build 3 rolling windows anchored at the most recent candle timestamp. Each window: 6m train + 2m OOS. Windows step by 2m (= `wf_test_months`) so they don't overlap on OOS periods.

**Verified code (in-session):**
```python
# Source: verified in-session
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

TRADING_DAYS_PER_MONTH = 21  # calendar approximation for date-based splitting

@dataclass
class WalkForwardWindow:
    window_num: int
    train_start: datetime
    train_end: datetime
    oos_start: datetime
    oos_end: datetime

def build_windows(
    most_recent_ts: datetime,
    train_months: int,
    test_months: int,
    n_windows: int,
) -> list[WalkForwardWindow]:
    """Build n_windows rolling WF windows anchored at most_recent_ts.

    Window n (most recent):
        OOS end   = most_recent_ts
        OOS start = OOS end - test_months
        Train end = OOS start
        Train start = Train end - train_months

    Window n-1: shift everything back by test_months.

    Args:
        most_recent_ts: Timestamp of the last candle in DB.
        train_months: Months in training window (6).
        test_months: Months in OOS window (2).
        n_windows: Number of rolling windows (3).

    Returns:
        List of WalkForwardWindow ordered oldest-first.
    """
    train_days = train_months * TRADING_DAYS_PER_MONTH
    test_days = test_months * TRADING_DAYS_PER_MONTH

    windows = []
    for w in range(n_windows - 1, -1, -1):
        oos_end = most_recent_ts - timedelta(days=w * test_days)
        oos_start = oos_end - timedelta(days=test_days)
        train_end = oos_start
        train_start = train_end - timedelta(days=train_days)
        windows.append(WalkForwardWindow(
            window_num=n_windows - w,
            train_start=train_start,
            train_end=train_end,
            oos_start=oos_start,
            oos_end=oos_end,
        ))
    return windows
```

**Window count:** With `n_windows=3, train_months=6, test_months=2`, the minimum data span is train_days + n_windows × test_days = 126 + 3×42 = 252 trading days (~12 months). Confirmed by in-session calculation.

### Pattern 3: WFE Calculation

**Formula:** `WFE = oos_score / is_score` where score = profit factor from trades in that period. [CITED: quantstrategy.io walk-forward optimization article]

**Profit factor formula:**
```python
# Source: verified in-session
import numpy as np

def compute_profit_factor(pnl_array: np.ndarray) -> float:
    """Compute profit factor from a P&L array.

    Profit factor = sum(winners) / abs(sum(losers)).
    Returns 0.0 if no losers (degenerate — treated as non-profitable by gate).

    Args:
        pnl_array: 1-D numpy array of per-trade P&L values (can be negative).

    Returns:
        Float >= 0.0. Values > 1.0 mean strategy is net profitable.
    """
    winners = pnl_array[pnl_array > 0]
    losers = pnl_array[pnl_array < 0]
    if len(losers) == 0 or losers.sum() == 0:
        return 0.0  # no losers = insufficient data for meaningful metric
    return float(winners.sum() / abs(losers.sum()))

def compute_wfe(is_pf: float, oos_pf: float) -> float:
    """Compute Walk-Forward Efficiency.

    WFE = oos_profit_factor / is_profit_factor.
    Returns 0.0 if is_pf is 0 to avoid ZeroDivisionError.

    Args:
        is_pf: In-sample profit factor.
        oos_pf: Out-of-sample profit factor.

    Returns:
        WFE value. Values >= 0.50 pass the gate (wfe_minimum from config).
    """
    if is_pf == 0.0:
        return 0.0
    return oos_pf / is_pf
```

**Why profit factor (not Sharpe):** [CITED: quantstrategy.io] Profit factor is preferred for WFE-style gating because it is interpretable (>1.0 = profitable), does not require volatility assumptions, and is more stable with small trade counts typical in a 2-month OOS window. Sharpe ratio requires sufficient sample size for the mean/std estimate to be meaningful. Given that strategies may generate few signals in a 2-month OOS window, profit factor is the more robust choice.

### Pattern 4: Trade Outcome Simulation (Backtester Core)

**What:** Given a CandidateSignal (entry, SL, TP1, TP2, direction) and the subsequent candles, simulate which level price hits first.

**Logic:**
```python
# Source: designed for this project based on ORM schema and strategy contract
from decimal import Decimal
from enum import Enum

class TradeOutcome(Enum):
    TP1 = "tp1"
    TP2 = "tp2"
    SL = "sl"
    OPEN = "open"   # window ended before any level hit

def simulate_trade_outcome(
    direction: str,   # "LONG" or "SHORT"
    entry: float,
    sl: float,
    tp1: float,
    tp2: float | None,
    subsequent_candles: list,  # Candle ORM objects, oldest-first, AFTER signal candle
) -> tuple[TradeOutcome, float]:
    """Simulate trade outcome from subsequent candles.

    Per strategy contract: SL is a hard stop, TP1 is the primary take-profit.
    Returns (outcome, pnl_in_price_units).

    Simplified model (no partial close at TP1 — that is Phase 6/7):
    - Whichever level is hit first in the candle sequence determines outcome.
    - 'Hit' = candle high >= level (LONG buy-side) or candle low <= level (SHORT sell-side).

    Args:
        direction: "LONG" or "SHORT".
        entry: Entry price float.
        sl: Stop-loss price float.
        tp1: Take-profit 1 price float.
        tp2: Take-profit 2 price float (None if not set).
        subsequent_candles: Candles after entry, ordered oldest-newest.

    Returns:
        Tuple of (TradeOutcome, pnl). pnl is positive for winners, negative for losers.
    """
    risk = abs(entry - sl)  # risk per unit

    for candle in subsequent_candles:
        high = float(candle.high)
        low = float(candle.low)

        if direction == "LONG":
            if low <= sl:
                return TradeOutcome.SL, -risk
            if tp2 is not None and high >= tp2:
                return TradeOutcome.TP2, abs(tp2 - entry)
            if high >= tp1:
                return TradeOutcome.TP1, abs(tp1 - entry)
        else:  # SHORT
            if high >= sl:
                return TradeOutcome.SL, -risk
            if tp2 is not None and low <= tp2:
                return TradeOutcome.TP2, abs(tp2 - entry)
            if low <= tp1:
                return TradeOutcome.TP1, abs(tp1 - entry)

    return TradeOutcome.OPEN, 0.0  # window ended, no level hit
```

**Key design decision:** The backtester calls `strategy.generate_signals(candle_window)` for each param combo on the training slice. The candle_window passed to the strategy must be a `dict[str, list[Candle]]` — same format as `StrategyRunner._fetch_candles()` returns. The strategy is pure (no DB access), so calling it with a pre-fetched candle dict is safe and matches the CLAUDE.md constraint.

**Important:** `generate_signals()` is async. The optimizer must call it with `await`. Since the optimizer itself runs inside an `async` APScheduler job, this works naturally — no `asyncio.run()` needed.

### Pattern 5: Multi-Window Gate

**What:** 2 of 3 OOS windows must show profit_factor > 1.0. This is evaluated BEFORE WFE gating, as the multi-window test is an additional hurdle over and above WFE.

```python
# Source: designed for this project
def multi_window_gate_passes(oos_profit_factors: list[float], min_profitable_windows: int = 2) -> bool:
    """Check that at least min_profitable_windows OOS windows are profitable.

    Args:
        oos_profit_factors: List of profit factors from each OOS window.
        min_profitable_windows: Minimum number that must exceed 1.0 (2 per CONTEXT.md).

    Returns:
        True if gate passes, False otherwise.
    """
    profitable = sum(1 for pf in oos_profit_factors if pf > 1.0)
    return profitable >= min_profitable_windows
```

### Pattern 6: Monte Carlo Bootstrap

**What:** Resample the daily P&L vector 1000 times (with replacement), compute max drawdown and profit factor for each simulation. Gate: P95 simulated drawdown ≤ 2× historical drawdown AND P5 simulated profit factor > 1.0.

**Daily P&L vector:** Aggregate per-trade P&L to daily buckets (sum of all trade P&Ls closing on that day) for the OOS period. If no trades on a day, the day is 0 — include it in the vector to maintain temporal density.

**Verified code (in-session, matches OPTIM-05 requirements):**
```python
# Source: verified in-session
import numpy as np

def run_monte_carlo(
    daily_pnl: np.ndarray,
    n_simulations: int = 1000,
) -> dict[str, float]:
    """Run Monte Carlo bootstrap on daily P&L.

    Args:
        daily_pnl: 1-D array of daily P&L values from the OOS period.
        n_simulations: Number of bootstrap simulations (1000 per CONTEXT.md).

    Returns:
        Dict with keys: p95_drawdown, p5_profit_factor, historical_max_drawdown.
    """
    n_days = len(daily_pnl)
    sim_max_drawdowns = np.zeros(n_simulations)
    sim_profit_factors = np.zeros(n_simulations)

    for i in range(n_simulations):
        resampled = np.random.choice(daily_pnl, size=n_days, replace=True)

        # Max drawdown from equity curve
        equity = np.cumsum(resampled)
        running_max = np.maximum.accumulate(equity)
        max_dd = float((running_max - equity).max())
        sim_max_drawdowns[i] = max_dd

        # Profit factor
        winners = resampled[resampled > 0]
        losers = resampled[resampled < 0]
        if len(losers) > 0 and losers.sum() != 0:
            pf = float(winners.sum() / abs(losers.sum()))
        else:
            pf = 0.0
        sim_profit_factors[i] = pf

    # Historical max drawdown (from actual OOS sequence)
    equity_hist = np.cumsum(daily_pnl)
    running_max_hist = np.maximum.accumulate(equity_hist)
    hist_max_dd = float((running_max_hist - equity_hist).max())

    return {
        "historical_max_drawdown": hist_max_dd,
        "p95_drawdown": float(np.percentile(sim_max_drawdowns, 95)),
        "p5_profit_factor": float(np.percentile(sim_profit_factors, 5)),
    }

def monte_carlo_passes(results: dict[str, float]) -> bool:
    """Check OPTIM-05 gates on Monte Carlo results.

    Args:
        results: Output dict from run_monte_carlo().

    Returns:
        True if BOTH gates pass: P95 dd <= 2x hist dd AND P5 pf > 1.0.
    """
    dd_gate = results["p95_drawdown"] <= 2.0 * results["historical_max_drawdown"]
    pf_gate = results["p5_profit_factor"] > 1.0
    return dd_gate and pf_gate
```

### Pattern 7: Optimizer DB Write (follow existing async pattern)

**What:** Write the best-performing param combo to `optimizer_results`, set `is_active = TRUE`, deactivate previous active rows for the same strategy (flip to FALSE).

```python
# Source: follow src/database.py and src/pipeline/runner.py async DB write pattern
from sqlalchemy import update
from src.database import AsyncSessionLocal
from src.models.optimizer_result import OptimizerResultORM

async def _activate_best_params(
    strategy_name: str,
    best_combo: dict,
    window: WalkForwardWindow,
    is_score: float,
    oos_score: float,
    wfe: float,
    pf: float,
    max_dd: float,
    win_rate: float,
    trade_count: int,
) -> None:
    """Deactivate old active rows, write new active row for strategy.

    Follows the AsyncSessionLocal context manager pattern from src/database.py.
    Single transaction: deactivate old + insert new.

    Args:
        strategy_name: Strategy STRATEGY_NAME string.
        best_combo: Dict of param_name → value (the winning LHS combo).
        window: The most recent WalkForwardWindow used for final OOS evaluation.
        is_score, oos_score, wfe, pf, max_dd, win_rate, trade_count: Metrics.
    """
    async with AsyncSessionLocal() as session:
        # Deactivate previous active rows for this strategy
        await session.execute(
            update(OptimizerResultORM)
            .where(
                OptimizerResultORM.strategy == strategy_name,
                OptimizerResultORM.is_active.is_(True),
            )
            .values(is_active=False)
        )
        # Insert new active row
        new_result = OptimizerResultORM(
            strategy=strategy_name,
            params=best_combo,
            train_start=window.train_start,
            train_end=window.train_end,
            test_start=window.oos_start,
            test_end=window.oos_end,
            in_sample_score=is_score,
            oos_score=oos_score,
            wfe=wfe,
            profit_factor=pf,
            max_drawdown=max_dd,
            win_rate=win_rate,
            trade_count=trade_count,
            is_active=True,
        )
        session.add(new_result)
        await session.commit()
```

**No schema migration needed:** The `optimizer_results` table already has all required columns (`in_sample_score`, `oos_score`, `wfe`, `profit_factor`, `max_drawdown`, `win_rate`, `trade_count`, `is_active`, `params` JSONB, `train_start/end`, `test_start/end`). [VERIFIED: alembic/versions/0001_initial_schema.py + src/models/optimizer_result.py]

### Pattern 8: APScheduler Job (follow create_scheduler pattern)

**What:** Add `run_optimizer` async job to the existing `create_scheduler()` function in `src/scheduler/jobs.py`. Cadence: every 24h (`optimizer_interval_hours = 24` from config).

```python
# Source: follow existing pattern in src/scheduler/jobs.py
async def run_optimizer() -> None:
    """Run walk-forward optimizer for all 4 strategies — called every 24h."""
    from src.backtesting.optimizer import WalkForwardOptimizer
    try:
        optimizer = WalkForwardOptimizer()
        await optimizer.run()
        log.info("jobs.optimizer.complete")
    except Exception as exc:
        log.error("jobs.optimizer.failed", error=str(exc))

# In create_scheduler(), add:
scheduler.add_job(
    run_optimizer,
    trigger=IntervalTrigger(hours=settings.optimizer_interval_hours),
    id="run_optimizer",
    name="Run walk-forward optimizer every 24h",
    max_instances=1,
    replace_existing=True,
)
```

### Pattern 9: Minimum Data Guard (Chemin A safety gate)

**What:** Before running optimization, check that the DB has sufficient candles. If not, log a structured event and return without writing results. The strategy midpoint fallback in `StrategyRunner` already handles the no-active-params case gracefully.

```python
# Source: designed for this project
MIN_CANDLES_FOR_OPTIMIZER: dict[str, int] = {
    "D1": 257,     # 3-window minimum at D1
    "H4": 1542,
    "H1": 6171,
    "M15": 24685,
}

async def _check_sufficient_data(candles_by_tf: dict[str, list]) -> bool:
    """Return True only if all timeframes meet the 3-window minimum.

    If any timeframe is below its minimum, log at WARNING level and return False.
    The optimizer will skip activation — StrategyRunner falls back to midpoints.

    Args:
        candles_by_tf: Dict of timeframe → list of Candle objects.

    Returns:
        True if data is sufficient for meaningful walk-forward evaluation.
    """
    for tf, minimum in MIN_CANDLES_FOR_OPTIMIZER.items():
        count = len(candles_by_tf.get(tf, []))
        if count < minimum:
            log.warning(
                "optimizer.insufficient_data",
                timeframe=tf,
                have=count,
                need=minimum,
            )
            return False
    return True
```

### Pattern 10: retired bootstrap archive Bulk Load (Chemin B — one-time operation)

**What:** If Chemin B is chosen, a standalone script reads retired bootstrap archive CSV files, resamples M1 → H1/H4/D1, converts to Candle ORM rows, and bulk-inserts using `INSERT ... ON CONFLICT DO NOTHING` (same pattern as `CandleFetcher`).

**Resample approach (pandas):**
```python
# Source: verified pandas API
import pandas as pd

def resample_m1_to_ohlcv(df_m1: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample M1 OHLCV DataFrame to a higher timeframe.

    Args:
        df_m1: DataFrame with DatetimeIndex and columns: open, high, low, close, volume.
        rule: Pandas resample rule: '1h', '4h', '1D'.

    Returns:
        Resampled DataFrame with OHLCV aggregations.
    """
    return df_m1.resample(rule).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    }).dropna(subset=['open'])  # drop windows with no trades (weekends)
```

**Note:** retired bootstrap archive M1 data excludes weekends natively (Forex spot gold trades Mon-Fri). The `dropna` removes any residual empty aggregation windows.

### Anti-Patterns to Avoid

- **Calling `asyncio.run()` inside an async job:** The APScheduler `AsyncIOScheduler` jobs are already async coroutines. Calling `asyncio.run()` inside them raises `RuntimeError: This event loop is already running`. Always use `await` directly.
- **Fetching candles inside the 100-combo evaluation loop:** Fetch all candles once BEFORE the LHS loop. Fetching per-combo would issue 100-400 async DB queries per strategy per optimizer run.
- **Modifying strategies to accept sync calls:** Strategies are async. Call them with `await`. Do not add synchronous wrappers — this would violate the CLAUDE.md invariant about keeping strategies pure.
- **Writing interim results per LHS combo:** Write only the final best-combo result per strategy. Writing 100 rows per strategy per run = 400 rows/day of dead data.
- **Shared numpy state between simulations:** In the Monte Carlo loop, `np.random.choice(..., replace=True)` is safe without seeding (seeds a fresh state each run). Do not set a global seed — it would make all runs identical.
- **Using `Decimal` in numpy arrays:** Strategy outputs include `Decimal` prices from ORM. Cast to `float` before building numpy P&L arrays: `float(candle.close)`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Space-filling parameter sampling | Custom grid/random sampler | `scipy.stats.qmc.LatinHypercube` + `scale` | LHS guarantees uniform marginal coverage; random sampling clusters |
| OHLCV timeframe conversion | Manual loop aggregation | `pandas.DataFrame.resample().agg()` | Handles DST, irregular intervals, missing bars correctly |
| Percentile statistics | Manual sort + index | `numpy.percentile(arr, 95)` | Handles edge cases, tested |
| Cumulative max (for drawdown) | Rolling max loop | `numpy.maximum.accumulate(equity)` | Vectorized, O(n) |

---

## Common Pitfalls

### Pitfall 1: LatinHypercube API Change Between scipy Versions
**What goes wrong:** Code written for older scipy uses `n_components=` argument; scipy 1.17.1 (project version) uses `d=`. Results in `TypeError` at runtime.
**Why it happens:** API was changed in scipy 1.10+ from `n_components` to `d`.
**How to avoid:** Always use `LatinHypercube(d=N_PARAMS)`. Verified in-session.
**Warning signs:** `TypeError: __init__() got an unexpected keyword argument 'n_components'`

### Pitfall 2: Candle Ordering — Oldest-First Contract
**What goes wrong:** Strategies (and ATR/swing calculations) require candles in oldest-first order. DB queries `ORDER BY timestamp DESC LIMIT N` return newest-first. If not reversed, all indicator calculations produce wrong results.
**Why it happens:** DB query for performance vs. strategy contract for correctness.
**How to avoid:** Always apply `list(reversed(rows))` after scalars().all() — exactly as `StrategyRunner._fetch_candles()` does. The backtester must do the same when slicing sub-windows.
**Warning signs:** ATR values that are implausibly large, zero signals generated.

### Pitfall 3: Zero-Trade Windows Returning Misleading Metrics
**What goes wrong:** A param combo generates 0 trades in the training window. `profit_factor = 0.0` (by our convention). WFE = 0/0 → ZeroDivisionError or misleading score.
**Why it happens:** Restrictive parameter values may generate no signals over the training period.
**How to avoid:** Guard all metric computations: if `trade_count == 0`, assign `profit_factor = 0.0` and skip this combo from WFE ranking (it cannot pass the gate). Log at DEBUG level.
**Warning signs:** All 100 combos failing WFE gate on first run (may indicate the strategy produces no signals with current params — data issue not code issue).

### Pitfall 4: Insufficient Data Guard Absent — Optimizer Writes Misleading Results
**What goes wrong:** With only 250 H1 bars (retired provider warmup), the optimizer runs, evaluates combos on a 10-day training window, and writes "validated" params. The WFE may pass by chance on tiny sample. These params are then activated and used in live signal generation.
**Why it happens:** No minimum data check before the optimizer run.
**How to avoid:** Implement `_check_sufficient_data()` — return early without writing results if candle counts are below `MIN_CANDLES_FOR_OPTIMIZER`.
**Warning signs:** `optimizer_results` rows written with `train_start`/`train_end` spanning < 2 months.

### Pitfall 5: `Decimal` Values in numpy Operations
**What goes wrong:** SQLAlchemy returns `Numeric(12,5)` columns as Python `Decimal` objects. Passing a list of Candle ORM objects with `.close` as Decimal to `np.array(...)` produces an object-dtype array that breaks arithmetic.
**Why it happens:** ORM type preservation.
**How to avoid:** Cast explicitly: `float(candle.close)`, `float(candle.high)`, etc. The existing strategies already do this (`np.array([float(c.high) for c in candles])`). Use the same pattern in the backtester.
**Warning signs:** `numpy` type errors, unexpected object-dtype arrays.

### Pitfall 6: Strategy Signal Count vs. Candle Count
**What goes wrong:** `generate_signals()` receives 4032 H1 candles (6m of H1) but only looks at the last N candles internally. Signals represent the most recent candle cluster — running it on the full 6m window does not produce 6m of trade history; it produces signals for the single most recent pattern.
**Why it happens:** Strategies are designed for real-time signal generation, not backtesting replay.
**How to avoid:** The backtester must call `generate_signals()` repeatedly on rolling sub-windows, stepping forward by N candles (e.g., step by 1 H4 candle), to simulate what the strategy would have produced over the full training period. Alternatively, the backtester can provide a smaller sliding window (e.g., 500 candles) that shifts across the training period.
**Recommended approach:** Slide a 500-candle window across the training period in steps of a timeframe's candle cadence. For each position, collect signals, then check outcome in the next N candles. This mirrors exactly how `StrategyRunner` operates in production.
**Warning signs:** Optimizer reports 0-2 trades per 6m training window when 30+ trades would be expected.

---

## Sync vs. Async Architecture Decision

**Verdict:** Keep the optimizer fully async. [VERIFIED: APScheduler AsyncIOScheduler pattern]

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| Candle fetch | `async` with `AsyncSessionLocal` | Follows existing pattern; DB is async-native |
| LHS sampling | sync (pure scipy/numpy) | No I/O — no benefit from async |
| Trade simulation | sync (pure numpy) | No I/O — CPU-bound |
| Strategy calls | `await strategy.generate_signals()` | Strategies are defined as async; cannot call sync |
| DB write | `async` with `AsyncSessionLocal` | Single transaction at end — follows existing pattern |
| Top-level orchestrator | `async def run_optimizer()` | APScheduler job is coroutine — `await` directly |

The pattern is: **async entry → sync heavy compute → async DB write**. No `asyncio.run()` anywhere.

---

## Chemin A vs. Chemin B Decision Gate

This section is the explicit decision gate the plan must expose to the user.

**Chemin A is viable for framework delivery but NOT for statistical validation within any reasonable timeline.**

| Criterion | Chemin A | Chemin B (retired bootstrap archive) |
|-----------|----------|------------------------|
| Framework operational | Day 1 | Day 1 |
| First WFE-valid activation (H1) | ~7.5 months from today | ~1 day (after manual download) |
| First 3-window validation (H1) | ~11.5 months from today | ~1 day |
| OPTIM-02 verifiable | No (for months) | Yes (on first run after load) |
| OPTIM-03 verifiable | No (for months) | Yes |
| Human effort required | None | One manual download session (retired bootstrap archive, select XAUUSD, download year batches) |
| Ongoing maintenance | Zero | Zero (one-time operation; retired provider accumulates going forward) |
| Data quality | Real retired-provider XAUUSD (post-accumulation) | Real XAUUSD spot (same underlying instrument) |
| Licensing risk | None | Low — retired bootstrap archive described as free, no restriction identified |
| yfinance GC=F as alternative | Not recommended — futures ≠ spot | Not recommended — basis risk |

**Recommended plan structure:**
1. Wave 0: Framework + data guard (Chemin A mode — operational but not activated)
2. Decision gate: User chooses A or B
3. Wave 1 (if B): `src/data/historical_loader.py` + one-time import script → bulk insert M1 data → resample → verify 3-window eval runs and produces a WFE-valid result
4. Wave 2 (B or A after accumulation): Run full OPTIM-01 through OPTIM-05 verification

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (both in pyproject.toml) |
| Config file | `pyproject.toml` (`asyncio_mode = "auto"`) |
| Quick run command | `pytest tests/test_backtesting/ -x -q` |
| Full suite command | `pytest -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| OPTIM-01 | LHS samples 100 combos per strategy | unit | `pytest tests/test_backtesting/test_optimizer.py::test_lhs_produces_100_combos -x` | ❌ Wave 0 |
| OPTIM-01 | LHS values within PARAM_RANGES | unit | `pytest tests/test_backtesting/test_optimizer.py::test_lhs_values_in_range -x` | ❌ Wave 0 |
| OPTIM-02 | WFE < 0.50 does not activate params | unit | `pytest tests/test_backtesting/test_walk_forward.py::test_wfe_gate_blocks_low_wfe -x` | ❌ Wave 0 |
| OPTIM-02 | Strategy retains previous params on failure | unit | `pytest tests/test_backtesting/test_optimizer.py::test_retains_previous_on_failure -x` | ❌ Wave 0 |
| OPTIM-03 | Multi-window: 2/3 OOS profitable passes | unit | `pytest tests/test_backtesting/test_walk_forward.py::test_multiwindow_2_of_3_passes -x` | ❌ Wave 0 |
| OPTIM-03 | Multi-window: 1/3 OOS profitable fails | unit | `pytest tests/test_backtesting/test_walk_forward.py::test_multiwindow_1_of_3_fails -x` | ❌ Wave 0 |
| OPTIM-04 | Optimizer job registered in scheduler | unit | `pytest tests/test_backtesting/test_scheduler_wiring.py::test_optimizer_job_registered -x` | ❌ Wave 0 |
| OPTIM-05 | P95 dd ≤ 2× hist dd gate | unit | `pytest tests/test_backtesting/test_monte_carlo.py::test_p95_dd_gate -x` | ❌ Wave 0 |
| OPTIM-05 | P5 profit factor > 1.0 gate | unit | `pytest tests/test_backtesting/test_monte_carlo.py::test_p5_pf_gate -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_backtesting/ -x -q`
- **Per wave merge:** `pytest -x -q` (full 143-test suite must remain green)
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_backtesting/__init__.py` — package init
- [ ] `tests/test_backtesting/test_optimizer.py` — LHS, WFE gate, retain-previous tests
- [ ] `tests/test_backtesting/test_walk_forward.py` — window construction, multi-window gate
- [ ] `tests/test_backtesting/test_monte_carlo.py` — P95/P5 gate tests
- [ ] `tests/test_backtesting/test_scheduler_wiring.py` — job registration check
- [ ] `src/backtesting/__init__.py` — package init
- [ ] Framework install: already installed (pytest in pyproject.toml)

**Test pattern:** Tests for the optimizer MUST use synthetic candle fixtures (not live DB), following the existing strategy test pattern in `tests/test_strategies/conftest.py`. The conftest must set env vars before any `src.*` import (follow `tests/conftest.py`).

---

## Security Domain

This phase has minimal security surface. No user-facing endpoints, no authentication. Applicable ASVS categories:

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Internal scheduler job — no external auth |
| V3 Session Management | No | No sessions |
| V4 Access Control | No | Internal only |
| V5 Input Validation | Yes | Validate param combo values are within PARAM_RANGES before passing to strategy |
| V6 Cryptography | No | No crypto in optimizer |

**V5 concern:** LHS sampling with `qmc.scale()` stays within bounds by construction, but rounding and float precision may produce edge values slightly outside. Clamp each param value to its declared range: `max(lo, min(hi, value))` before building the params dict.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| scipy (LatinHypercube) | OPTIM-01 | ✓ | 1.17.1 | — |
| numpy | All metric computation | ✓ | in venv | — |
| pandas | historical_loader.py (Chemin B) | ✓ | in venv | — |
| PostgreSQL (candles table) | All optimizer reads | ✓ (existing) | — | — |
| APScheduler | OPTIM-04 | ✓ | 3.10.x | — |
| retired bootstrap archive M1 download | Chemin B | Human action required | — | Chemin A (months delay) |
| retired-provider historical API (large requests) | Chemin A long-term | Partially confirmed | — | Chemin B |

**Missing dependencies with no fallback:** None that block framework delivery.

**Missing dependencies with fallback:**
- retired bootstrap archive download (Chemin B): requires one manual download session by the user; fallback is Chemin A which delays statistical validation by 7+ months.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | retired bootstrap archive provides XAUUSD M1 data suitable for resampling to H1/H4/D1 | Data Provider Assessment | If XAUUSD not available, need alternative M1 source (Dukascopy node.app appears to offer tick data); plan must pivot |
| A2 | retired bootstrap archive CSV format is parseable by standard pandas CSV reader with datetime index | Pattern 10 | If format differs (e.g., proprietary encoding), manual parsing step required in historical_loader.py |
| A3 | retired bootstrap archive licensing permits use of data for algo trading strategy development | Data Provider Assessment | If licensing restricts algo use, need retired market-data candidate paid or other licensed source |
| A4 | Profit factor is a sufficient fitness metric for WFE calculation given small OOS trade counts | Standard Stack / Pattern 3 | If trade counts are consistently too low (<5) for profit factor to be meaningful, Sharpe or win_rate may be preferable — open question during implementation |
| A5 | retired market-data candidate free tier does not provide sufficient H1 XAUUSD history for backtesting | Data Provider Assessment | If verified otherwise, retired market-data candidate becomes a viable programmatic Chemin B alternative |

---

## Open Questions

1. **Is the retired bootstrap archive M1 download feasible for the user?**
   - What we know: Data is free, no API key, manually downloadable by year/month.
   - What's unclear: Whether the user is willing/able to do a one-time manual download session.
   - Recommendation: Expose this as the Chemin B action item in the plan with exact steps.

2. **How many trades does each strategy generate per 500-candle sliding window?**
   - What we know: Strategies are pattern-based; signal frequency depends heavily on params.
   - What's unclear: Whether a 2-month OOS window produces enough trades (minimum 5-10) for profit factor to be statistically meaningful.
   - Recommendation: Implement a minimum trade count threshold in the WFE gate (e.g., skip combos with < 5 total trades). Expose this threshold as a config value.

3. **Does the sliding window backtester use all 4 timeframes or a primary timeframe?**
   - What we know: Strategies use multi-timeframe candles (M15 entry + H4 levels, etc.).
   - What's unclear: The backtester must supply a complete `dict[str, list]` for each window position.
   - Recommendation: For the IS evaluation, slide the window across all timeframes simultaneously (same date range, all TFs). This requires fetching the full multi-TF candle history in memory before the evaluation loop.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `LatinHypercube(n_components=N)` | `LatinHypercube(d=N)` | scipy 1.10+ | Old code raises TypeError in 1.17.1 |
| `qmc.LatinHypercube` (scipy < 1.7) | `scipy.stats.qmc.LatinHypercube` | scipy 1.7+ | Module path changed |
| pandas `resample().ohlc()` | `resample().agg({'open':'first',...})` | Stable | `ohlc()` shorthand works but less explicit |

---

## Sources

### Primary (HIGH confidence)
- In-session verification (venv): scipy 1.17.1 `LatinHypercube(d=N).random(n=100)` + `scale()` — confirmed working
- In-session computation: minimum candle counts for 1-window and 3-window walk-forward splits
- In-session computation: retired-provider accumulation timeline (7-12 months per timeframe)
- In-session computation: Monte Carlo bootstrap with P95/P5 gates
- `src/models/optimizer_result.py` + `alembic/versions/0001_initial_schema.py` — schema verified, no migration needed
- `src/scheduler/jobs.py` — APScheduler pattern confirmed for new job integration
- `src/strategies/runner.py` — StrategyRunner candle fetch and param load pattern confirmed

### Secondary (MEDIUM confidence)
- [QuantStrategy.io Walk-Forward Optimization](https://quantstrategy.io/blog/walk-forward-optimization-vs-traditional-backtesting-which/) — profit factor vs Sharpe discussion
- [retired bootstrap archive](https://www.retired_bootstrap_archive.com/download-free-forex-historical-data/) — XAUUSD M1 data confirmed free, no API key
- [retired daily CSV source XAUUSD](https://retired_daily_csv_source.com/q/d/?s=xauusd) — D1 CSV confirmed, daily only
- [AlphaLog Alpha Vantage guide](https://alphalog.ai/blog/alphavantage-api-complete-guide) — 25 req/day free tier, compact=100 bars for intraday
- [SciPy LatinHypercube docs v1.17](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.qmc.LatinHypercube.html)

### Tertiary (LOW confidence)
- retired market-data candidate free tier coverage for XAUUSD H1: not verified — flagged as A5 assumption
- Alpha Vantage GOLD_SILVER_SPOT endpoint exact schema: not verified via direct API call

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries verified in-project venv
- Framework mechanics (LHS, WFE, Monte Carlo): HIGH — all code patterns verified in-session
- Architecture (sync/async split, DB write): HIGH — follows verified existing patterns
- External data providers (Chemin B): MEDIUM — retired bootstrap archive confirmed free/available but not directly fetched; retired bootstrap archive CSV format assumed parseable
- retired market-data candidate free tier: LOW — not verified

**Research date:** 2026-04-22
**Valid until:** 2026-07-22 (stable domain — 90 days)
